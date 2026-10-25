---
title: "Slow and Steady: a local AI file tidier for my inner sloth"
published: false
tags: devchallenge, hf26challenge
---

*This is a submission for the [Hacktoberfest Open-Source AI Challenge Week 3: Slow and Steady](https://dev.to/challenges/hacktoberfest-week3-2026-10-19).*

## What I Built

My downloads folder is a list of decisions I keep postponing. **Slow and Steady** is a local AI assistant that suggests where files belong, so I can review a list instead of sorting everything manually.

It uses **Qwen2.5 1.5B through Ollama** to classify filenames into categories such as finance, work, personal and reading. An invoice and meeting notes can both be text files while belonging in different folders.

The workflow is: **plan → review → approve → tidy → undo**. Planning never moves existing files. Applying displays the proposed paths and requires approval tied to that exact plan. Uncertain files stay put.

## Demo

**DEMO LINK TO ADD:** Add a short recording showing a dry run, approval, file movement and undo.

To try the disposable sample inbox after installing Python and local Ollama:

~~~text
ollama pull qwen2.5:1.5b
python -m sloth_tidy demo demo-inbox
python -m sloth_tidy plan demo-inbox
~~~

The planner saves a JSON file. Review it and pass its path to:

~~~text
python -m sloth_tidy apply "PATH_TO_SAVED_PLAN"
~~~

The displayed approval token is required before any files move.

## Code

**PUBLIC REPOSITORY LINK TO ADD:** Upload the project source, README and MIT license to a public repository, then replace this note with its URL.

The project uses Python 3.11+ and has no third-party Python runtime dependencies. Setup and undo instructions are included in the README.

## How I Built It

Ollama's [structured output API](https://docs.ollama.com/capabilities/structured-outputs) returns category decisions. The model sees filenames, suffixes, byte counts and integer IDs. File contents and absolute inbox paths are excluded from requests.

The model cannot choose arbitrary paths or run commands. Python validates every decision, constructs destinations under the inbox and checks file hashes before moving anything. Existing destinations are refused. Recent files, partial downloads, links and junctions are left alone.

A journal records intent before each move. Exclusive hard-link creation followed by removal of the original directory entry avoids overwriting destination data. Approved undo can restore the original paths and reconcile an interrupted move on supported filesystems.

The existing automated suite completed with **27 passing tests and one skipped symlink test** because Windows denied symlink-creation privileges. The separate junction test passed. Tests cover the HTTP contract, approval, collisions, stale files and interrupted undo. Model accuracy and latency have not been independently benchmarked.

## Why Does Open Innovation Matter?

The open pieces provide the actual semantic planner: [Ollama](https://github.com/ollama/ollama) runs inference, and the default [Qwen2.5 1.5B weights](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct) use Apache 2.0. This assistant's source is MIT-licensed.

After downloading the model and disabling Ollama's cloud features, the workflow can run offline. Personal filenames need not visit a hosted model. There is no per-request API charge, although hardware and electricity still cost resources.

I can inspect the prompt, edit categories or replace the local model while keeping the same approval and movement checks. That control over data location, model choice and behavior matters more for this chore than access to a hosted model. Small-model judgment still needs human review.

## Related Community Reading

🦥 [sloth-sorter: a lazy file tidier that runs Gemma locally](https://dev.to/ghalmeidadev/sloth-sorter-a-lazy-file-tidier-that-runs-gemma-locally-21lc), by [@ghalmeidadev](https://dev.to/ghalmeidadev), reports an imperfect classification in its small demo—a useful reminder to review suggestions. [Read Full Discussion on DEV](https://dev.to/ghalmeidadev/sloth-sorter-a-lazy-file-tidier-that-runs-gemma-locally-21lc).

⚙️ [Ollama Structured Outputs in Practice — Getting Type-Safe JSON from Local LLMs with Pydantic](https://dev.to/jangwook_kim_e31e7291ad98/ollama-structured-outputs-in-practice-getting-type-safe-json-from-local-llms-with-pydantic-m38), by [@jangwook_kim_e31e7291ad98](https://dev.to/jangwook_kim_e31e7291ad98), discusses schema-based output and small-model limitations; this project uses a standard-library validator. [Read Full Discussion on DEV](https://dev.to/jangwook_kim_e31e7291ad98/ollama-structured-outputs-in-practice-getting-type-safe-json-from-local-llms-with-pydantic-m38).

*AI assistance disclosure: this project and post draft were created with AI assistance.*
