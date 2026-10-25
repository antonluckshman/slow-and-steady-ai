"""A preview-first command line interface."""

import argparse
import json
import os
from pathlib import Path
import sys
import time

from .core import (DEST, apply_plan, approval_token, config, create_plan, demo_classify,
                   read_journal, read_json, root_path, safe_path, save_plan, undo_journal,
                   validate_plan)
from .ollama import Ollama, TidyError


def quoted(value):
    # Escape terminal control characters, including those in untrusted filenames/reasons.
    return json.dumps(str(value), ensure_ascii=True)


def preview(plan):
    validate_plan(plan)
    print(f"Inbox: {quoted(plan['root'])}")
    print(f"Planner: {plan['engine']} / {quoted(plan['model'] or 'NON-AI demo rules')}")
    print(f"Plan: {approval_token(plan)} | {len(plan['moves'])} proposed moves")
    for move in plan["moves"]:
        target = f"{DEST}/{move['category']}/{move['name']}"
        print(f"  {quoted(move['name'])} -> {quoted(target)} "
              f"({move['confidence']:.0%} self-reported confidence)")
        print(f"    {quoted(move['reason'])}")
    print(f"Skipped/kept: {len(plan['skipped'])}")
    for item in plan["skipped"]:
        print(f"  KEEP {quoted(item['name'])}: {quoted(item['reason'])}")


def confirmation(action, plan, supplied):
    expected = f"{action} {approval_token(plan)}"
    if supplied is not None:
        return supplied
    if not sys.stdin.isatty():
        raise TidyError(f"Approval required. Review the preview, then pass --approve {quoted(expected)}.")
    print("No original file will be overwritten. Review every proposed path above.")
    try:
        return input(f"Type {expected} to continue (anything else cancels): ").strip()
    except EOFError:
        return ""


def make_demo(path):
    path = safe_path(path)
    path.mkdir()  # Refuse to change an existing directory.
    examples = {
        "invoice-electricity.txt": "FAKE DEMO INVOICE. Amount: 0.00\n",
        "meeting-notes-garden-project.txt": "FAKE DEMO NOTES. Water the plants.\n",
        "travel-packing-list.txt": "FAKE DEMO LIST. Socks, book, hat.\n",
        "book-python-guide.txt": "FAKE DEMO READING NOTE. Learn at your own pace.\n",
        "photo-caption-holiday.txt": "FAKE DEMO PHOTO CAPTION. A sleepy sloth.\n",
        "mystery.txt": "FAKE DEMO FILE. Too little context to classify.\n",
        "still-downloading.part": "FAKE INCOMPLETE DOWNLOAD\n",
    }
    for name, content in examples.items():
        target = path / name
        target.write_text(content, encoding="utf-8")
        old = time.time() - 600
        os.utime(target, (old, old))
    print(f"Created disposable demo inbox: {quoted(path)}")


def parser():
    p = argparse.ArgumentParser(description="Slow and Steady: local AI, approved file tidying.")
    sub = p.add_subparsers(dest="command", required=True)
    for command in ("doctor", "plan"):
        s = sub.add_parser(command, help="Check local Ollama" if command == "doctor" else "Dry run: save suggestions only")
        s.add_argument("--config", type=Path, help="Optional TOML configuration")
        s.add_argument("--model", help="Override the locally downloaded model")
        s.add_argument("--url", help="Override the loopback Ollama URL")
        if command == "plan":
            s.add_argument("inbox", type=Path)
            s.add_argument("--demo-rules", action="store_true", help="Explicit NON-AI smoke-test mode")
    s = sub.add_parser("demo", help="Create a new disposable sample inbox")
    s.add_argument("inbox", type=Path)
    for command, document in (("show", "plan"), ("apply", "plan"), ("undo", "journal")):
        s = sub.add_parser(command)
        s.add_argument(document, type=Path)
        if command != "show":
            s.add_argument("--approve", help="Explicit MOVE/UNDO token from preview, for noninteractive use")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "demo":
            make_demo(args.inbox)
        elif args.command in {"plan", "doctor"}:
            cfg = config(args.config)
            for key in ("model", "url"):
                if getattr(args, key):
                    cfg[key] = getattr(args, key)
            if args.command == "plan":
                root_path(args.inbox)  # Fail before contacting Ollama on an unsafe inbox.
            client = Ollama(cfg["model"], cfg["url"], cfg["timeout_seconds"])
            if args.command == "doctor":
                print(f"Local Ollama reachable; model installed: {quoted(client.doctor())}")
                print("For offline inference, start Ollama with OLLAMA_NO_CLOUD=1.")
            else:
                if not args.demo_rules:
                    client.doctor()
                    print("Asking the local model for categories; this can take a few minutes...", flush=True)
                classifier = demo_classify if args.demo_rules else client.classify
                plan = create_plan(args.inbox, cfg, classifier, "demo-rules" if args.demo_rules else "ollama")
                path = save_plan(plan)
                preview(plan)
                print(f"Dry run complete. No files moved. Saved plan: {quoted(path)}")
                print("Review with: python -m sloth_tidy show " + quoted(path))
                print("Apply with:  python -m sloth_tidy apply " + quoted(path))
        elif args.command in {"show", "apply"}:
            plan = read_json(args.plan)
            preview(plan)
            if args.command == "apply":
                if not plan["moves"]:
                    print("Nothing to move.")
                    return 0
                approval = confirmation("MOVE", plan, args.approve)
                journal = apply_plan(plan, approval)
                print(f"Moved {len(plan['moves'])} files. Journal: {quoted(journal)}")
                print("To restore: python -m sloth_tidy undo " + quoted(journal))
        elif args.command == "undo":
            plan, pending, _ = read_journal(args.journal)
            print(f"Restore to inbox: {quoted(plan['root'])}")
            for index in pending:
                move = plan["moves"][index]
                print(f"  RESTORE {quoted(DEST + '/' + move['category'] + '/' + move['name'])} "
                      f"-> {quoted(move['name'])}")
            if not pending:
                print("All journaled moves are already undone.")
                return 0
            approval = confirmation("UNDO", plan, args.approve)
            count = undo_journal(args.journal, approval)
            print(f"Restored/reconciled {count} files. Journal retained for audit.")
        return 0
    except (TidyError, OSError, ValueError, TypeError, KeyError) as exc:
        print("Error: " + quoted(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted. If an apply/undo was in progress, inspect its journal and use undo.", file=sys.stderr)
        return 130
