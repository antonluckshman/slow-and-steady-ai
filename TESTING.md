# Verification report

Verified on **October 25, 2026**, using **Windows and Python 3.14.0** on a local filesystem with hard-link support.

## Automated suite

```text
python -m unittest discover -s tests -v

Ran 28 tests
OK (skipped=1)
```

**27 passed, 1 skipped.** Windows did not grant symbolic-link creation privileges, so the symlink integration case was skipped. The separate Windows junction test ran and passed. The configured GitHub Actions matrix covers Python 3.11/3.14 on Windows, Linux and macOS, but those hosted jobs have not been run here.

The suite checks:

- Dry runs preserve source files; missing or mismatched approval blocks moves.
- Editing a plan invalidates its approval token.
- Normal apply/undo preserves bytes, identity and modification times.
- Repeated undo is safe; replaying an executed plan is refused.
- Same-size edits with restored timestamps are detected by SHA-256.
- Whole-plan preflight prevents partial movement when a source is stale or a destination exists.
- Exclusive destination creation never overwrites an existing file.
- Hidden, recent, partial, nested and oversized files are left alone; run limits and low-confidence keeps work.
- Unsafe filenames, traversal categories and malformed configuration are refused.
- Undo refuses changed destination data and newly occupied original paths.
- Recovery handles interruption after creating a hard link, an incomplete final journal append, partial execution and interruption during undo.
- An unsupported hard-link operation leaves the source intact.
- A damaged middle journal record is rejected.
- The operation lock blocks concurrent movement.
- A real loopback HTTP test server verifies the Ollama request format and complete classify/apply/undo workflow.
- Request payloads exclude file contents and absolute inbox paths; environment proxies are bypassed.
- Unknown categories, missing/duplicate IDs, bad confidence, truncated generations, HTTP redirects, non-loopback endpoints and cloud model names are refused.

## Documented command-line workflow

The CLI was also exercised as separate processes using disposable demo files and the explicit **non-AI** rules mode:

1. Create seven fake files and snapshot their content hashes.
2. Load the shipped TOML configuration and generate a plan: five proposed moves, two keeps.
3. Confirm that planning leaves all seven originals unchanged.
4. Show the saved plan; try applying without approval and confirm rejection.
5. Approve the exact plan token and verify five moves complete.
6. Approve undo and verify all seven original files return with identical content hashes.
7. Repeat undo and confirm it is harmless; attempt to replay the original plan and confirm rejection.

See [DEMO_OUTPUT.txt](DEMO_OUTPUT.txt) for the captured output, with machine paths and generated identifiers normalized. This demonstrates filesystem behavior, not AI judgment.

The delivery ZIP was checked for corruption, extracted to a separate disposable directory, and its `python -m sloth_tidy --help` entry point ran successfully without project-specific import settings.

## What was not verified

- **Real Ollama inference:** no Ollama executable or running daemon was available. `doctor` returned a connection-refused error with setup guidance. No model was downloaded. The HTTP contract was tested with the local test server; actual model accuracy, latency, memory needs and offline behavior still require the README's live demo on a machine with Ollama installed.
- **Other operating systems/Python versions:** the source targets Python 3.11+ and includes a CI matrix, but only Windows/Python 3.14 was executed locally.
- **Power-loss recovery, disk failure and malicious concurrent filesystem swaps:** journals are flushed and simulated process interruptions are covered, but the application is not a backup system or an operating-system sandbox.
- **Optional package installation:** direct module execution and the extracted source archive were verified; building/installing a wheel was not required or exercised.
- **Challenge eligibility/template/deadline:** DevRelay research required an unavailable sign-in. The project follows the supplied challenge brief; the DEV draft deliberately includes no unverified deadline or entry-status claim. No DEV article or challenge entry was submitted.

The model and API setup instructions were checked against primary sources: [Ollama chat API](https://docs.ollama.com/api/chat), [structured output](https://docs.ollama.com/capabilities/structured-outputs), [local-only configuration](https://docs.ollama.com/faq#how-do-i-disable-ollamas-cloud-features), and [the default model's listing](https://ollama.com/library/qwen2.5:1.5b).
