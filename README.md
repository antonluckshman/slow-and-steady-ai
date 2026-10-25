# Slow and Steady 🦥

A local-first lazy automation assistant for the **Slow and Steady** challenge: let a small open-weight model sort out your inbox, then approve the moves when you feel like it.

It uses **Ollama + Qwen2.5 1.5B** to understand filenames. An invoice and a meeting note can both be `.txt` files; the model assigns them to different semantic categories. Python enforces the boundaries. The model never receives filesystem tools or chooses destination paths.

**Plan → review → approve → tidy → undo.** Running `plan` never moves an existing file. It only reads eligible files locally for integrity checks and writes a JSON plan under the inbox's `.sloth-tidy/` folder.

## Requirements

- Python **3.11 or newer** on Windows, macOS or Linux. No Python runtime packages or API keys required.
- [Ollama](https://ollama.com/download), running locally, for AI planning.
- A downloaded local model. The default [qwen2.5:1.5b](https://ollama.com/library/qwen2.5:1.5b) is approximately **986 MB**; allow additional RAM for inference. CPU inference works, with speed depending on your hardware. No performance benchmark is claimed here.
- A local filesystem that supports **hard links**, such as NTFS, ext4 or APFS. The app deliberately stops if hard links are unavailable; FAT/exFAT and some network or synced folders are unsuitable.

Extract the project, open a terminal in this folder, and use `python` below. On systems where the command is named `python3`, substitute that name.

## 1. Start local AI

Install Ollama using its [official instructions](https://docs.ollama.com/quickstart), then download the default model:

```text
ollama pull qwen2.5:1.5b
```

For local-only operation, disable Ollama's cloud features before starting its server. Quit an already-running Ollama desktop app first so the server inherits this setting.

Windows PowerShell, in a separate terminal:

```powershell
$env:OLLAMA_NO_CLOUD = "1"
ollama serve
```

macOS/Linux, in a separate terminal:

```sh
OLLAMA_NO_CLOUD=1 ollama serve
```

Keep that terminal open. If Ollama reports the port is already in use, quit the existing instance, then retry. For persistent desktop configuration, see the [Ollama FAQ](https://docs.ollama.com/faq#how-do-i-disable-ollamas-cloud-features).

In your project terminal:

```text
python -m sloth_tidy doctor
```

This checks reachability and the installed model, without generating text. The app uses literal loopback addresses, ignores proxy environment variables, refuses HTTP redirects and refuses cloud model names. The daemon's own cloud configuration remains your responsibility; a loopback URL alone does not guarantee that a model runs locally.

## 2. Try the disposable demo

```text
python -m sloth_tidy demo demo-inbox
python -m sloth_tidy plan demo-inbox
```

The demo creates seven explicitly fake files in a **new** folder. Existing folders are refused. Sample files are given an old modification time so they pass the five-minute settling rule.

Planning shows suggestions such as:

```text
"invoice-electricity.txt" -> "_tidied/finance/invoice-electricity.txt"
"meeting-notes-garden-project.txt" -> "_tidied/work/meeting-notes-garden-project.txt"
```

Actual AI decisions vary. Unclear names can be kept. Model confidence is a self-reported hint, not a calibrated probability or safety guarantee.

**No Ollama yet?** Exercise the same preview, approval, movement and undo code using the explicitly labeled non-AI demo planner:

```text
python -m sloth_tidy plan demo-inbox --demo-rules
```

Demo rules are only a smoke test. AI mode never silently falls back to them after an error.

## 3. Review and approve

The `plan` command prints the absolute path to its saved JSON plan. Replace `PLAN_PATH` in these commands with that path, retaining quotes:

```text
python -m sloth_tidy show "PLAN_PATH"
python -m sloth_tidy apply "PLAN_PATH"
```

`apply` displays the proposed paths again. Type the exact `MOVE <12-character-token>` it shows to approve this plan. Any other response cancels. There is no generic `--yes` flag.

For a noninteractive terminal, review the plan first, then provide its exact approval text:

```text
python -m sloth_tidy apply "PLAN_PATH" --approve "MOVE TOKEN_FROM_PREVIEW"
```

The token identifies the plan contents. Editing a plan changes its token. It is an approval aid, not a signature or authentication mechanism. Keep the inbox and its local state under your control.

Files stay inside the same inbox:

```text
demo-inbox/
  _tidied/
    finance/invoice-electricity.txt
    work/meeting-notes-garden-project.txt
  .sloth-tidy/
    plan-<id>.json
    journal-<id>.jsonl
```

To tidy a real folder, pass its explicit path instead of `demo-inbox`. Start with copies of files in a dedicated inbox. The app refuses your home directory and its ancestors.

## 4. Undo

`apply` prints the journal path. Substitute it below:

```text
python -m sloth_tidy undo "JOURNAL_PATH"
```

Undo previews the reverse moves and asks for `UNDO <token>`. It restores original filenames, refuses changed files or occupied original paths, and can be repeated safely. Empty category folders and the audit journal remain.

If an operation stops partway, use its journal to undo the completed moves. Intent is recorded and flushed before each move. A move creates an exclusive hard link at the destination, verifies the file, then removes the old directory entry; it never replaces existing destination data. If interrupted between those steps, undo recognizes the two links and reconciles them. Source bytes are not rewritten.

A process killed without cleanup may leave `.sloth-tidy/operation.lock`. Confirm no other run is active, remove **only that lock file**, then run undo on the interrupted journal. Do not delete the journal. Power loss and filesystem failure are outside the tested recovery guarantee; this is not a backup system.

## Practical defaults and boundaries

| Setting | Default |
| --- | --- |
| Scope | Top-level regular files only; no recursion |
| Settling time | Leave files modified in the last 5 minutes |
| Per-run limit | 100 eligible files |
| File size cap | 256 MiB |
| AI batch size | 8 filenames per request |
| Confidence threshold | 0.80; keep uncertain files |
| Categories | finance, work, personal, media, reading, software |
| Destination | `_tidied/<category>/<original filename>` in the same inbox |

Hidden files, partial downloads, directories, special files and files with existing multiple hard links are skipped. Symlinks, Windows junctions and other reparse points are refused, including in ancestor and destination paths. Some cloud-synced placeholders are reparse points and will therefore be refused. Filename conflicts are never resolved by overwriting or inventing new names.

All proposed sources are checked before the first move using SHA-256, size, modification time and filesystem identity. Each source is checked again as it moves. One stale file aborts the initial preflight. Errors during execution stop the run and retain a recovery journal. Concurrent apply/undo runs are blocked by the inbox lock. Avoid writers changing the inbox during an operation; the path checks are not an operating-system sandbox against a malicious process racing to replace directories.

Only **filename, suffix, byte count and an integer ID**, plus category descriptions, go to Ollama. File bytes, absolute paths and content hashes do not. File contents are read locally for integrity checks, never interpreted as instructions. Filenames are untrusted data; strict response validation accepts only known IDs and configured categories. Any missing, duplicate, invalid or truncated model output aborts planning. No shell tools are exposed to the model.

Plans and journals contain filenames and local paths, so treat them as private. No watcher, scheduled mover, telemetry or remote service is included. A scheduled job can run `plan`; a person can approve it later.

## Customize

The app uses built-in defaults unless you explicitly pass a config file:

```text
python -m sloth_tidy plan "YOUR_INBOX" --config sloth.toml
python -m sloth_tidy plan "YOUR_INBOX" --model qwen2.5:7b
```

Edit [sloth.toml](sloth.toml) for categories, limits and model settings. Category keys must be safe lowercase names. Descriptions guide semantic classification. `keep` is reserved. Download an alternative model before using it; check its license and hardware requirements separately. Larger models can produce better suggestions at a higher memory and latency cost.

If responses are incomplete, reduce `batch_size` to 4 or 1, increase the timeout for slow CPUs, or try a more capable local model. If a destination exists or a file changes, create a fresh plan rather than bypassing the check. A plan can be executed only once, even after undo.

## Why open innovation matters

The open pieces do the actual work: Ollama runs the local inference server and Qwen2.5's open weights supply the semantic classifications. This application is MIT-licensed; [Ollama is MIT-licensed](https://github.com/ollama/ollama/blob/main/LICENSE), and the default [Qwen2.5 1.5B Instruct weights use Apache 2.0](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct/blob/main/LICENSE). Model licenses are separate from this project's license; do not assume every Qwen size uses the same terms.

After the initial installation and model download, inference can work without internet when the local daemon is configured as above. Personal filenames stay on your machine instead of being sent to a hosted model. There is no per-request API bill, although hardware, electricity and initial downloads still cost resources.

You can inspect the exact prompt and validator, change category descriptions, replace the model, or experiment with your own locally fine-tuned model without changing the move engine. Compared with a hosted closed model, this design gives control over data location, availability and model choice. The tradeoff is owning the model setup and accepting modest small-model judgment. Human review stays part of the design.

## Development and verification

```text
python -m unittest discover -s tests -v
```

Tests use disposable folders and a real local HTTP test server that imitates Ollama's API contract. They cover approval, stale hashes, collisions, path traversal, model validation, privacy of request payloads, partial failures and interrupted undo. No real user files or external model are needed. See [TESTING.md](TESTING.md) for the exact verified scope and limitations.

Source: [planner and move engine](sloth_tidy/core.py), [Ollama client](sloth_tidy/ollama.py), [CLI](sloth_tidy/cli.py). Optional command installation: `python -m pip install .`, then `sloth-tidy --help`; installation may download the build tooling, while direct module execution requires no package download.

The [DEV post draft](DEV_POST_DRAFT.md) explains the challenge idea. Add your public repository/demo links and verify the organizer's current template before submitting. No challenge deadline or eligibility claim is made here. Ollama API details follow the official [chat API](https://docs.ollama.com/api/chat) and [structured output](https://docs.ollama.com/capabilities/structured-outputs) documentation.
