"""Constrained plans, stale-file checks, exclusive moves and durable undo journals."""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import time
import tomllib
import uuid

from .ollama import TidyError, validate_decisions

STATE = ".sloth-tidy"
DEST = "_tidied"
DEFAULT_CATEGORIES = {
    "finance": "Invoices, receipts, taxes, budgets and financial paperwork",
    "work": "Meeting notes, project briefs and work documents",
    "personal": "Household, travel and personal documents",
    "media": "Photos, music, video and creative assets",
    "reading": "Books, articles, guides and learning material",
    "software": "Installers, software packages and source archives",
}
DEFAULTS = {"model": "qwen2.5:1.5b", "url": "http://127.0.0.1:11434",
            "min_age_seconds": 300, "max_files": 100, "max_size_mb": 256,
            "batch_size": 8, "min_confidence": 0.80, "timeout_seconds": 180,
            "categories": DEFAULT_CATEGORIES}
PARTIAL = {".part", ".crdownload", ".download", ".tmp", ".lock"}


def now():
    return datetime.now(timezone.utc).isoformat()


def config(path=None):
    result = {**DEFAULTS, "categories": dict(DEFAULT_CATEGORIES)}
    if path:
        with Path(path).open("rb") as stream:
            supplied = tomllib.load(stream)
        if set(supplied) - set(DEFAULTS):
            raise TidyError("Unknown configuration option.")
        result.update(supplied)
    validate_categories(result["categories"])
    limits = {"min_age_seconds": (0, 86400 * 365), "max_files": (1, 10000),
              "max_size_mb": (1, 4096), "batch_size": (1, 16), "timeout_seconds": (1, 3600)}
    for key, (low, high) in limits.items():
        if type(result[key]) is not int or not low <= result[key] <= high:
            raise TidyError(f"{key} must be an integer between {low} and {high}.")
    v = result["min_confidence"]
    if type(v) not in {int, float} or not math.isfinite(v) or not 0 <= v <= 1:
        raise TidyError("min_confidence must be between 0 and 1.")
    return result


def validate_categories(categories):
    if not isinstance(categories, dict) or not 1 <= len(categories) <= 16:
        raise TidyError("Provide between 1 and 16 categories.")
    for name, description in categories.items():
        if (not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", name)
                or name in {"keep", "con", "prn", "aux", "nul"}
                or re.fullmatch(r"(?:com|lpt)[0-9]", name)
                or not isinstance(description, str) or not 1 <= len(description) <= 200):
            raise TidyError("Use short lowercase category names and descriptions; keep is reserved.")


def absolute(path):
    # abspath, not resolve: resolving first would hide symlinks/junctions.
    return Path(os.path.abspath(os.path.expanduser(path)))


def safe_path(path):
    path = absolute(path)
    for node in reversed((path, *path.parents)):
        try:
            info = node.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise TidyError(f"Links, junctions and reparse points are refused: {node}")
    return path


def root_path(path):
    root = safe_path(path)
    if not root.is_dir():
        raise TidyError("The inbox must be an existing directory.")
    home = absolute(str(Path.home()))
    if root == home or root in home.parents or root == Path(root.anchor):
        raise TidyError("Choose a dedicated inbox folder, not your home or a filesystem root.")
    return root


def valid_name(name):
    if (not isinstance(name, str) or not name or name.startswith(".")
            or name in {STATE, DEST} or any(c in name for c in '/\\:<>"|?*')
            or any(ord(c) < 32 or ord(c) == 127 for c in name)
            or name.endswith((".", " ")) or len(name.encode("utf-8")) > 240):
        raise TidyError("Unsafe or nonportable filename in plan.")
    if name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(10)],
                                      *[f"LPT{i}" for i in range(10)]}:
        raise TidyError("Reserved filename in plan.")
    return name


def fingerprint(path):
    safe_path(path)
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise TidyError(f"Not a regular file: {path}")
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
        after = os.fstat(stream.fileno())
    fields = ("st_size", "st_mtime_ns", "st_dev", "st_ino")
    if any(getattr(before, f) != getattr(after, f) for f in fields):
        raise TidyError(f"File changed while reading: {path.name}")
    # Confirm the name still refers to the file we hashed.
    current = path.lstat()
    if any(getattr(current, f) != getattr(after, f) for f in fields):
        raise TidyError(f"File replaced while reading: {path.name}")
    return {"sha256": digest, "size": after.st_size, "mtime_ns": after.st_mtime_ns,
            "device": after.st_dev, "inode": after.st_ino}


def unchanged(path, expected):
    if fingerprint(path) != expected:
        raise TidyError(f"File changed since preview: {path}. Create a new plan.")


def scan(root, cfg):
    files, skipped = [], []
    for path in sorted(root.iterdir(), key=lambda p: (p.name.casefold(), p.name)):
        if path.name in {STATE, DEST}:
            continue
        reason = None
        try:
            valid_name(path.name)
            safe_path(path)
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                reason = "directory or special file (no recursion)"
            elif info.st_nlink > 1:
                reason = "file already has multiple hard links"
            elif path.suffix.lower() in PARTIAL:
                reason = "temporary or incomplete download"
            elif time.time() - info.st_mtime < cfg["min_age_seconds"]:
                reason = "recently modified; leave it to settle"
            elif info.st_size > cfg["max_size_mb"] * 1024 * 1024:
                reason = "over size limit"
            elif len(files) >= cfg["max_files"]:
                reason = "per-run file limit"
            else:
                files.append({"id": len(files), "name": path.name,
                              "fingerprint": fingerprint(path)})
        except (OSError, TidyError) as exc:
            reason = str(exc)
        if reason:
            skipped.append({"name": path.name, "reason": reason})
    return files, skipped


def demo_classify(files, categories):
    """Explicit non-AI smoke-test mode; never silently used after an AI failure."""
    rules = {"finance": ("invoice", "receipt", "budget"), "work": ("meeting", "project"),
             "personal": ("travel", "holiday"), "reading": ("guide", "book"),
             "media": ("photo", ".jpg", ".png", ".mp3"), "software": ("installer", ".zip")}
    result = []
    for f in files:
        category = next((k for k, words in rules.items()
                         if k in categories and any(w in f["name"].lower() for w in words)), "keep")
        result.append({"id": f["id"], "category": category, "confidence": 0.95 if category != "keep" else 0,
                       "reason": "Demo keyword rule; no AI inference"})
    return result


def create_plan(root, cfg, classifier, engine="ollama"):
    root = root_path(root)
    files, skipped = scan(root, cfg)
    decisions = []
    for start in range(0, len(files), cfg["batch_size"]):
        batch = files[start:start + cfg["batch_size"]]
        metadata = [{"id": f["id"], "name": f["name"], "suffix": Path(f["name"]).suffix,
                     "bytes": f["fingerprint"]["size"]} for f in batch]
        decisions.extend(validate_decisions(classifier(metadata, cfg["categories"]), metadata, cfg["categories"]))
    by_id = {d["id"]: d for d in decisions}
    moves = []
    for f in files:
        decision = by_id[f["id"]]
        if decision["category"] == "keep" or decision["confidence"] < cfg["min_confidence"]:
            skipped.append({"name": f["name"], "reason": "kept: " + decision["reason"]})
            continue
        dest = safe_path(root / DEST / decision["category"] / f["name"])
        # Check case-insensitive conflicts even when running on a case-sensitive disk.
        if dest.exists() or (dest.parent.exists() and any(
                p.name.casefold() == dest.name.casefold() for p in dest.parent.iterdir())):
            skipped.append({"name": f["name"], "reason": "destination already exists"})
            continue
        moves.append({"name": f["name"], "category": decision["category"],
                      "confidence": decision["confidence"], "reason": decision["reason"],
                      "fingerprint": f["fingerprint"]})
    return {"version": 1, "id": uuid.uuid4().hex, "created_at": now(), "root": str(root),
            "engine": engine, "model": cfg["model"] if engine == "ollama" else None,
            "categories": cfg["categories"], "moves": moves, "skipped": skipped}


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()


def approval_token(plan):
    return hashlib.sha256(canonical(plan)).hexdigest()[:12]


def state_dir(root):
    state = safe_path(root / STATE)
    state.mkdir(exist_ok=True)
    if not state.is_dir():
        raise TidyError("The state path is not a directory.")
    return state


def write_json_exclusive(path, value):
    safe_path(path)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=True, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def save_plan(plan):
    root = validate_plan(plan)
    path = state_dir(root) / f"plan-{plan['id']}.json"
    write_json_exclusive(path, plan)
    return path


def read_json(path):
    path = safe_path(path)
    if path.stat().st_size > 10_000_000:
        raise TidyError("Plan is too large.")
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def validate_plan(plan):
    if not isinstance(plan, dict) or set(plan) != {
            "version", "id", "created_at", "root", "engine", "model", "categories", "moves", "skipped"}:
        raise TidyError("Unexpected plan structure.")
    if plan["version"] != 1 or not isinstance(plan["id"], str) or not re.fullmatch(r"[a-f0-9]{32}", plan["id"]):
        raise TidyError("Unsupported plan version or invalid id.")
    if not isinstance(plan["root"], str) or not Path(plan["root"]).is_absolute():
        raise TidyError("Plan root must be absolute.")
    root = root_path(plan["root"])
    validate_categories(plan["categories"])
    if (plan["engine"] not in {"ollama", "demo-rules"}
            or not isinstance(plan["created_at"], str)
            or (plan["model"] is not None and not isinstance(plan["model"], str))
            or not isinstance(plan["skipped"], list) or len(plan["skipped"]) > 100000):
        raise TidyError("Invalid planner metadata.")
    for item in plan["skipped"]:
        if (not isinstance(item, dict) or set(item) != {"name", "reason"}
                or not isinstance(item["name"], str) or not isinstance(item["reason"], str)):
            raise TidyError("Invalid skipped-file entry.")
    if not isinstance(plan["moves"], list) or len(plan["moves"]) > 10000:
        raise TidyError("Invalid move list.")
    seen = set()
    for move in plan["moves"]:
        if not isinstance(move, dict) or set(move) != {"name", "category", "confidence", "reason", "fingerprint"}:
            raise TidyError("Unexpected move structure.")
        name = valid_name(move["name"])
        if name.casefold() in seen or not isinstance(move["category"], str) or move["category"] not in plan["categories"]:
            raise TidyError("Duplicate filename or unknown category in plan.")
        seen.add(name.casefold())
        fp = move["fingerprint"]
        if (not isinstance(fp, dict) or set(fp) != {"sha256", "size", "mtime_ns", "device", "inode"}
                or not isinstance(fp["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", fp["sha256"])
                or any(type(fp[k]) is not int or fp[k] < 0 for k in ("size", "mtime_ns", "device", "inode"))):
            raise TidyError("Invalid file fingerprint.")
        if (type(move["confidence"]) not in {int, float} or not math.isfinite(move["confidence"])
                or not 0 <= move["confidence"] <= 1 or not isinstance(move["reason"], str)
                or not 1 <= len(move["reason"]) <= 200):
            raise TidyError("Invalid decision in plan.")
    return root


def move_paths(root, move):
    return (safe_path(root / move["name"]),
            safe_path(root / DEST / move["category"] / move["name"]))


@contextmanager
def operation_lock(root):
    path = state_dir(root) / "operation.lock"
    try:
        stream = path.open("x", encoding="utf-8")
    except FileExistsError as exc:
        raise TidyError(f"Another operation or interrupted run holds {path}. "
                        "If no run is active, remove this lock file and use undo on its journal.") from exc
    try:
        with stream:
            stream.write(str(os.getpid()))
        yield
    finally:
        path.unlink()


def append_event(stream, event):
    stream.write(json.dumps(event, ensure_ascii=True, allow_nan=False) + "\n")
    stream.flush()
    os.fsync(stream.fileno())


def exclusive_move(source, dest, expected):
    """Hard-link then unlink: destination creation is atomic and NEVER overwrites.

    Both paths must be on a filesystem with hard-link support. Caller journals intent first.
    A crash between steps leaves two links to the same data; undo reconciles that state.
    """
    unchanged(source, expected)
    safe_path(dest)
    os.link(source, dest, follow_symlinks=False)
    unchanged(dest, expected)
    unchanged(source, expected)
    source.unlink()


def apply_plan(plan, approval):
    root = validate_plan(plan)
    if approval != "MOVE " + approval_token(plan):
        raise TidyError("Approval did not match this plan. No files moved.")
    if not plan["moves"]:
        raise TidyError("This plan has no moves.")
    journal = state_dir(root) / f"journal-{plan['id']}.jsonl"
    with operation_lock(root):
        # Preflight the entire plan before the first move.
        if journal.exists():
            raise TidyError("This plan already has an execution journal. Create a fresh plan.")
        for move in plan["moves"]:
            source, dest = move_paths(root, move)
            unchanged(source, move["fingerprint"])
            if dest.exists() or (dest.parent.exists() and any(
                    p.name.casefold() == dest.name.casefold() for p in dest.parent.iterdir())):
                raise TidyError(f"Destination already exists: {dest}. No files moved.")
        with journal.open("x", encoding="utf-8") as stream:
            append_event(stream, {"event": "header", "plan": plan, "at": now()})
            try:
                for index, move in enumerate(plan["moves"]):
                    source, dest = move_paths(root, move)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    safe_path(dest)
                    append_event(stream, {"event": "intent", "index": index})
                    exclusive_move(source, dest, move["fingerprint"])
                    append_event(stream, {"event": "done", "index": index})
                append_event(stream, {"event": "complete", "at": now()})
            except (OSError, TidyError) as exc:
                raise TidyError(f"Stopped safely; some moves may have completed. Undo journal: {journal}. "
                                f"Cause: {exc}") from exc
    return journal


def read_journal(path):
    path = safe_path(path)
    if path.stat().st_size > 20_000_000:
        raise TidyError("Journal is too large.")
    raw = path.read_bytes()
    lines = raw.splitlines(keepends=True)
    events = []
    for index, line in enumerate(lines):
        try:
            events.append(json.loads(line))
        except (ValueError, UnicodeError) as exc:
            # A power loss can leave an unfinished final append. Intent was fsynced before a move.
            if index == len(lines) - 1 and not line.endswith(b"\n"):
                break
            raise TidyError("Journal is damaged; inspect it before recovery.") from exc
    if not events or not isinstance(events[0], dict) or events[0].get("event") != "header":
        raise TidyError("Missing journal header.")
    plan = events[0].get("plan")
    root = validate_plan(plan)
    if path != root / STATE / f"journal-{plan['id']}.jsonl":
        raise TidyError("Journal location does not match its plan.")
    intended, undone = set(), set()
    for event in events[1:]:
        if not isinstance(event, dict) or event.get("event") not in {"intent", "done", "undo_intent", "undone", "complete"}:
            raise TidyError("Invalid journal event.")
        if event["event"] == "complete":
            continue
        number = event.get("index")
        if type(number) is not int or not 0 <= number < len(plan["moves"]):
            raise TidyError("Invalid journal index.")
        if event["event"] == "intent":
            intended.add(number)
        elif number not in intended:
            raise TidyError("Journal event without a prior intent.")
        if event["event"] == "undone":
            undone.add(number)
    return plan, sorted(intended - undone, reverse=True), raw


def undo_state(root, move):
    source, dest = move_paths(root, move)
    if source.exists():
        unchanged(source, move["fingerprint"])
        if dest.exists():
            unchanged(dest, move["fingerprint"])
            if not os.path.samefile(source, dest):
                raise TidyError("Both paths exist but are different files; refusing recovery.")
            return "both", source, dest
        return "original", source, dest
    if not dest.exists():
        raise TidyError(f"Neither original nor destination exists: {move['name']}")
    unchanged(dest, move["fingerprint"])
    return "moved", source, dest


def undo_journal(path, approval):
    path = absolute(path)
    plan, pending, _ = read_journal(path)
    if approval != "UNDO " + approval_token(plan):
        raise TidyError("Undo approval did not match. No files moved.")
    root = root_path(plan["root"])
    with operation_lock(root):
        plan2, pending, raw = read_journal(path)
        if plan2 != plan:
            raise TidyError("Journal changed during approval.")
        for index in pending:
            undo_state(root, plan["moves"][index])
        # Discard only a torn final event before appending recovery events.
        if raw and not raw.endswith(b"\n"):
            last = raw.rfind(b"\n") + 1
            try:
                json.loads(raw[last:])
                repaired = raw + b"\n"
            except (ValueError, UnicodeError):
                repaired = raw[:last]
            with path.open("r+b") as stream:
                stream.seek(0)
                stream.write(repaired)
                stream.truncate()
                stream.flush()
                os.fsync(stream.fileno())
        with path.open("a", encoding="utf-8") as stream:
            for index in pending:
                move = plan["moves"][index]
                status, source, dest = undo_state(root, move)
                append_event(stream, {"event": "undo_intent", "index": index})
                if status == "moved":
                    exclusive_move(dest, source, move["fingerprint"])
                elif status == "both":
                    dest.unlink()
                append_event(stream, {"event": "undone", "index": index})
    return len(pending)
