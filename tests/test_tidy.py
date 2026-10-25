import copy
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from sloth_tidy import core
from sloth_tidy.cli import main
from sloth_tidy.ollama import Ollama, TidyError, validate_decisions


class FileSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "inbox"
        self.root.mkdir()
        self.cfg = core.config()

    def file(self, name="invoice.txt", text="original"):
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        old = time.time() - 600
        os.utime(path, (old, old))
        return path

    def plan(self):
        return core.create_plan(self.root, self.cfg, core.demo_classify, "demo-rules")

    def apply(self, plan):
        return core.apply_plan(plan, "MOVE " + core.approval_token(plan))

    def undo(self, journal):
        plan, _, _ = core.read_journal(journal)
        return core.undo_journal(journal, "UNDO " + core.approval_token(plan))

    def test_dry_run_saves_plan_without_moving(self):
        original = self.file()
        plan = self.plan()
        path = core.save_plan(plan)
        self.assertTrue(original.exists())
        self.assertFalse((self.root / core.DEST).exists())
        self.assertEqual(core.read_json(path), plan)
        self.assertEqual(len(plan["moves"]), 1)

    def test_approval_mismatch_never_moves(self):
        original = self.file()
        with self.assertRaises(TidyError):
            core.apply_plan(self.plan(), "yes")
        self.assertTrue(original.exists())
        self.assertFalse((self.root / core.DEST).exists())

    def test_apply_undo_and_replay_protection(self):
        original = self.file()
        expected = core.fingerprint(original)
        plan = self.plan()
        journal = self.apply(plan)
        dest = self.root / core.DEST / "finance" / original.name
        self.assertFalse(original.exists())
        self.assertEqual(core.fingerprint(dest), expected)
        self.assertEqual(self.undo(journal), 1)
        self.assertEqual(core.fingerprint(original), expected)
        self.assertFalse(dest.exists())
        self.assertEqual(self.undo(journal), 0)
        with self.assertRaises(TidyError):
            self.apply(plan)

    def test_same_size_same_mtime_edit_is_caught_by_hash(self):
        first = self.file("invoice-a.txt", "aaaa")
        second = self.file("invoice-b.txt", "bbbb")
        plan = self.plan()
        old = second.stat()
        second.write_text("cccc", encoding="utf-8")
        os.utime(second, ns=(old.st_atime_ns, old.st_mtime_ns))
        with self.assertRaises(TidyError):
            self.apply(plan)
        self.assertTrue(first.exists(), "all-file preflight must happen before any move")
        self.assertTrue(second.exists())

    def test_new_destination_aborts_whole_plan(self):
        source = self.file()
        plan = self.plan()
        dest = self.root / core.DEST / "finance" / source.name
        dest.parent.mkdir(parents=True)
        dest.write_text("do not overwrite", encoding="utf-8")
        with self.assertRaises(TidyError):
            self.apply(plan)
        self.assertEqual(dest.read_text(), "do not overwrite")
        self.assertTrue(source.exists())

    def test_collision_during_move_does_not_overwrite(self):
        source = self.file()
        dest = self.root / "destination.txt"
        expected = core.fingerprint(source)
        dest.write_text("existing", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            core.exclusive_move(source, dest, expected)
        self.assertEqual(dest.read_text(), "existing")
        self.assertTrue(source.exists())

    def test_existing_destination_is_skipped_by_planner(self):
        source = self.file()
        dest = self.root / core.DEST / "finance" / source.name
        dest.parent.mkdir(parents=True)
        dest.write_text("existing", encoding="utf-8")
        plan = self.plan()
        self.assertFalse(plan["moves"])
        self.assertTrue(any("destination" in item["reason"] for item in plan["skipped"]))

    def test_scanner_keeps_recent_partial_hidden_nested_and_large(self):
        self.file(".secret")
        self.file("download.part")
        (self.root / "recent.txt").write_text("recent")
        (self.root / "nested").mkdir()
        (self.root / "nested" / "invoice.txt").write_text("nested")
        large = self.file("large.bin", "x" * (1024 * 1024 + 1))
        self.cfg["max_size_mb"] = 1
        files, skipped = core.scan(self.root, self.cfg)
        self.assertEqual(files, [])
        self.assertEqual(len(skipped), 5)
        self.assertTrue(large.exists())

    def test_max_files_and_confidence_keep(self):
        self.file("invoice-a.txt")
        self.file("invoice-b.txt")
        self.cfg["max_files"] = 1
        self.cfg["min_confidence"] = 0.99
        plan = self.plan()
        self.assertEqual(plan["moves"], [])
        self.assertEqual(len(plan["skipped"]), 2)

    def test_injected_model_category_and_plan_traversal_rejected(self):
        self.file()
        plan = self.plan()
        for name in ("../invoice.txt", "sub/invoice.txt", "sub\\invoice.txt", ".hidden", "CON.txt"):
            bad = copy.deepcopy(plan)
            bad["moves"][0]["name"] = name
            with self.subTest(name=name), self.assertRaises(TidyError):
                self.apply(bad)
        plan["moves"][0]["category"] = "../../outside"
        with self.assertRaises(TidyError):
            self.apply(plan)

    def test_undo_refuses_edited_destination(self):
        source = self.file()
        journal = self.apply(self.plan())
        dest = self.root / core.DEST / "finance" / source.name
        dest.write_text("edited after move", encoding="utf-8")
        with self.assertRaises(TidyError):
            self.undo(journal)
        self.assertFalse(source.exists())
        self.assertEqual(dest.read_text(), "edited after move")

    def test_undo_refuses_recreated_original(self):
        source = self.file()
        journal = self.apply(self.plan())
        source.write_text("new original", encoding="utf-8")
        with self.assertRaises(TidyError):
            self.undo(journal)
        self.assertEqual(source.read_text(), "new original")

    def test_undo_recovers_crash_between_link_and_unlink(self):
        source = self.file()
        plan = self.plan()
        dest = self.root / core.DEST / "finance" / source.name
        state = core.state_dir(self.root)
        journal = state / f"journal-{plan['id']}.jsonl"
        dest.parent.mkdir(parents=True)
        with journal.open("w", encoding="utf-8") as stream:
            core.append_event(stream, {"event": "header", "plan": plan})
            core.append_event(stream, {"event": "intent", "index": 0})
        os.link(source, dest)
        with journal.open("ab") as stream:
            stream.write(b'{"event":"do')
        self.assertEqual(self.undo(journal), 1)
        self.assertTrue(source.exists())
        self.assertFalse(dest.exists())
        self.assertEqual(self.undo(journal), 0)

    def test_undo_recovers_interrupted_undo(self):
        source = self.file()
        plan = self.plan()
        journal = self.apply(plan)
        dest = self.root / core.DEST / "finance" / source.name
        with journal.open("a", encoding="utf-8") as stream:
            core.append_event(stream, {"event": "undo_intent", "index": 0})
        os.link(dest, source)
        self.assertEqual(self.undo(journal), 1)
        self.assertEqual(source.read_text(), "original")
        self.assertFalse(dest.exists())

    def test_hardlink_unsupported_fails_without_deleting_source(self):
        source = self.file()
        plan = self.plan()
        with patch.object(core.os, "link", side_effect=OSError("hard links unavailable")):
            with self.assertRaises(TidyError):
                self.apply(plan)
        self.assertTrue(source.exists())
        journal = self.root / core.STATE / f"journal-{plan['id']}.jsonl"
        self.assertEqual(self.undo(journal), 1)

    def test_changed_plan_invalidates_approval(self):
        self.file()
        plan = self.plan()
        token = "MOVE " + core.approval_token(plan)
        plan["moves"][0]["category"] = "work"
        with self.assertRaises(TidyError):
            core.apply_plan(plan, token)

    def test_corrupt_journal_middle_line_refused(self):
        self.file()
        journal = self.apply(self.plan())
        with journal.open("ab") as stream:
            stream.write(b"broken\n{}\n")
        with self.assertRaises(TidyError):
            self.undo(journal)

    def test_partial_apply_retains_recoverable_journal(self):
        first = self.file("invoice-a.txt")
        second = self.file("invoice-b.txt")
        plan = self.plan()
        original_move = core.exclusive_move
        def fail_second(source, dest, expected):
            if source == second:
                raise OSError("simulated filesystem failure")
            return original_move(source, dest, expected)
        with patch.object(core, "exclusive_move", side_effect=fail_second):
            with self.assertRaisesRegex(TidyError, "Undo journal"):
                self.apply(plan)
        self.assertFalse(first.exists())
        self.assertTrue(second.exists())
        journal = self.root / core.STATE / f"journal-{plan['id']}.jsonl"
        self.undo(journal)
        self.assertTrue(first.exists())
        self.assertTrue(second.exists())

    def test_operation_lock_blocks_apply(self):
        source = self.file()
        plan = self.plan()
        with core.operation_lock(self.root):
            with self.assertRaises(TidyError):
                self.apply(plan)
        self.assertTrue(source.exists())

    def test_symlink_is_skipped_and_destination_link_refused(self):
        source = self.file()
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        link = self.root / "linked.txt"
        try:
            link.symlink_to(source)
        except OSError as exc:
            self.skipTest(f"Symlink creation unavailable: {exc}")
        plan = self.plan()
        self.assertNotIn(link.name, [m["name"] for m in plan["moves"]])
        (self.root / core.DEST).symlink_to(outside, target_is_directory=True)
        with self.assertRaises(TidyError):
            self.apply(plan)
        self.assertEqual(list(outside.iterdir()), [])

    @unittest.skipUnless(os.name == "nt", "Windows junction test")
    def test_windows_junction_is_refused(self):
        self.file()
        plan = self.plan()
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        junction = self.root / core.DEST
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
                                capture_output=True, text=True)
        if result.returncode:
            self.skipTest("Junction creation unavailable")
        try:
            with self.assertRaises(TidyError):
                self.apply(plan)
            self.assertEqual(list(outside.iterdir()), [])
        finally:
            junction.rmdir()  # Remove the junction itself, never recurse into its target.

    def test_plan_failure_does_not_write_state_or_move(self):
        source = self.file()
        def broken(files, categories):
            raise TidyError("simulated invalid AI response")
        with self.assertRaises(TidyError):
            core.create_plan(self.root, self.cfg, broken)
        self.assertTrue(source.exists())
        self.assertFalse((self.root / core.STATE).exists())

    def test_cli_noninteractive_requires_approval_and_handles_errors(self):
        source = self.file()
        plan = self.plan()
        path = core.save_plan(plan)
        with patch("sys.stdin", io.StringIO()), patch("sys.stdout", io.StringIO()), patch("sys.stderr", io.StringIO()):
            self.assertEqual(main(["apply", str(path)]), 2)
            self.assertTrue(source.exists())
            self.assertEqual(main(["apply", str(path), "--approve", "MOVE " + core.approval_token(plan)]), 0)
            self.assertEqual(main(["show", str(self.root / "missing.json")]), 2)

    def test_configuration_rejects_unsafe_categories_and_bad_ranges(self):
        path = Path(self.temp.name) / "bad.toml"
        for text in ('batch_size = 0', 'min_confidence = nan', '[categories]\n"../bad" = "bad"',
                     '[categories]\ncon = "bad"', 'unexpected = true'):
            path.write_text(text, encoding="utf-8")
            with self.subTest(text=text), self.assertRaises(TidyError):
                core.config(path)


@contextmanager
def mock_ollama(mode="ok"):
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            calls.append((self.path, None))
            self.respond({"models": [{"name": "qwen2.5:1.5b"}]})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((self.path, body))
            if mode == "redirect":
                self.send_response(302)
                self.send_header("Location", "http://example.com/")
                self.end_headers()
                return
            metadata = json.loads(body["messages"][1]["content"])
            decisions = [{"id": f["id"], "category": "finance", "confidence": 0.96,
                          "reason": "Invoice paperwork"} for f in metadata["files"]]
            if mode == "invalid":
                decisions[0]["category"] = "../../escape"
            if mode == "missing":
                decisions = []
            result = {"message": {"content": json.dumps({"decisions": decisions})}, "done": True}
            if mode == "truncated":
                result["done_reason"] = "length"
            self.respond(result)

        def respond(self, value):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(value).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield Ollama(url=f"http://127.0.0.1:{server.server_port}", timeout=5), calls
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


class OllamaIntegrationTests(unittest.TestCase):
    def test_http_client_schema_privacy_and_full_file_lifecycle(self):
        with tempfile.TemporaryDirectory() as temp, mock_ollama() as (client, calls):
            root = Path(temp).resolve() / "inbox"
            root.mkdir()
            source = root / "invoice.txt"
            source.write_text("PRIVATE_CONTENT_MUST_NOT_LEAVE_PROCESS", encoding="utf-8")
            cfg = core.config()
            cfg["min_age_seconds"] = 0
            with patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:1", "NO_PROXY": ""}):
                self.assertEqual(client.doctor(), "qwen2.5:1.5b")
            plan = core.create_plan(root, cfg, client.classify)
            self.assertEqual(plan["engine"], "ollama")
            self.assertTrue(source.exists())
            payload = calls[1][1]
            self.assertEqual(calls[1][0], "/api/chat")
            self.assertIs(payload["stream"], False)
            self.assertIsInstance(payload["format"], dict)
            self.assertNotIn("PRIVATE_CONTENT", json.dumps(payload))
            self.assertNotIn(str(root), json.dumps(payload))
            journal = core.apply_plan(plan, "MOVE " + core.approval_token(plan))
            core.undo_journal(journal, "UNDO " + core.approval_token(plan))
            self.assertEqual(source.read_text(), "PRIVATE_CONTENT_MUST_NOT_LEAVE_PROCESS")

    def test_invalid_missing_truncated_and_redirected_responses_fail_closed(self):
        files = [{"id": 0, "name": "invoice.txt", "bytes": 4, "suffix": ".txt"}]
        for mode in ("invalid", "missing", "truncated", "redirect"):
            with self.subTest(mode=mode), mock_ollama(mode) as (client, calls):
                with self.assertRaises(TidyError):
                    client.classify(files, core.DEFAULT_CATEGORIES)
                self.assertEqual(len(calls), 1)

    def test_remote_urls_and_cloud_models_refused(self):
        for url in ("https://ollama.com", "http://192.168.1.1:11434", "http://localhost.evil",
                    "http://user:pass@127.0.0.1", "http://127.0.0.1/api", "http://127.0.0.1/?x=1"):
            with self.subTest(url=url), self.assertRaises(TidyError):
                Ollama(url=url)
        with self.assertRaises(TidyError):
            Ollama(model="some-model:cloud")

    def test_bad_decision_ids_and_confidence(self):
        files = [{"id": 0}]
        valid = {"id": 0, "category": "work", "confidence": 0.9, "reason": "Meeting notes"}
        for replacement in ({"id": 1}, {"id": True}, {"confidence": float("nan")},
                            {"confidence": True}, {"reason": ""}, {"category": "../../oops"}, {"category": []}):
            with self.subTest(replacement=replacement), self.assertRaises(TidyError):
                validate_decisions([{**valid, **replacement}], files, core.DEFAULT_CATEGORIES)
        with self.assertRaises(TidyError):
            validate_decisions([valid, valid], files, core.DEFAULT_CATEGORIES)


if __name__ == "__main__":
    unittest.main()
