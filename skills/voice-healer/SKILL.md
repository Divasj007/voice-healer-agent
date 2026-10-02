---
name: voice-healer
description: Offline voice-controlled developer workflow that captures microphone commands with Whisper, speaks local feedback with pyttsx3, runs Python programs, and uses a local Ollama coding model to diagnose and repair failing Python code. Use when a developer asks to fix/heal/run a Python script, control local Git actions by voice or CLI, recover from a Python traceback, or demonstrate automated self-healing.
license: MIT
compatibility: Requires Python 3.11+, Ollama on localhost:11434 with qwen2.5-coder:1.5b for healing, faster-whisper/pyttsx3/SpeechRecognition/PyAudio for voice features, and a working Python execution environment. Runtime inference is local; initial package/model installation requires network access.
---

# Voice Healer

Voice Healer is a local developer skill for voice-driven Python execution and self-healing. It is designed for skills-compatible agent orchestrators that can execute bundled scripts from this skill directory.

## Activation triggers

Activate this skill when the user asks to:

- fix, heal, repair, debug, or automatically recover a failing Python script;
- run a Python file and react to its traceback;
- control local development commands with voice or manual CLI input;
- inspect Git status or diff, stage files, or create a local Git commit;
- demonstrate an offline/local coding agent or self-healing harness.

Do not activate it for destructive system administration or remote infrastructure operations that are outside the supported command set.

## Available scripts

Run scripts from the `scripts/` directory:

- `scripts/listen_command.py` captures microphone audio with `speech_recognition`/PyAudio, transcribes locally with `WhisperModel("tiny", device="cpu", compute_type="int8")`, and optionally speaks feedback with `pyttsx3`.
- `scripts/heal_code.py` executes one Python target, captures stdout/stderr, backs up a failing source file to `<file>.py.bak`, requests a repair from local Ollama using `qwen2.5-coder:1.5b`, validates the returned Python syntax, writes the patch atomically, and re-runs the target.
- `scripts/main.py` is the user-facing orchestrator. It routes `heal`, `run`, and a safe subset of local Git operations to the appropriate implementation and supports `--cli` for text-only operation.
- `scripts/safety_guard.py` statically inspects AI-generated Python candidates for newly introduced shell execution, network access, credential access, dynamic code execution, destructive deletion, unsafe deserialization, native-code loading, and unsafe path writes.
- `scripts/approval.py` powers the browser Lab's human approval flow: generate a reviewable repair proposal without writing it, verify the target hash before approval, apply the approved candidate atomically, back it up, and restore it if verification fails.
- `scripts/lifecycle.py` normalizes repair outcomes into explicit lifecycle stages so the UI can show analyze, diagnose, propose, approve, apply, verify, and terminal states consistently.

## Standard workflow

1. Identify the target Python file and keep paths inside the repository when using the main orchestrator.
2. Run the target and capture its failure diagnostics.
3. For interactive UI use, create a repair proposal without changing the target and review the generated diff.
4. Require explicit approval before applying a UI proposal; reject stale proposals when the target hash changes.
5. For trusted non-interactive CLI or pre-commit workflows, run the self-healing harness directly.
6. Preserve the original source backup created by the harness for review or rollback.
7. Feed the traceback and current source to the local Ollama coder model.
8. Reject repairs that are empty, unchanged, or syntactically invalid.
9. Write valid repairs atomically and execute the target again.
10. Accept a repair only when the target exits successfully within the configured timeout.
11. When every repair attempt fails, restore the original source and return a non-zero exit status.

## Voice and CLI commands

The orchestrator understands these command forms:

```text
heal <python-file>
fix <python-file>
run <python-file>
git status
git diff
git log
git add <file> [file ...]
git commit <message>
help
quit
```

The Git route intentionally supports only `status`, `diff`, `log`, `add`, and `commit`. Remote pushes, resets, checkouts, rebases, and arbitrary shell commands are not exposed by the voice parser.

## Failure handling

Voice input failures such as missing PyAudio, missing microphone devices, audio driver exceptions, unavailable Whisper dependencies, or interrupted input should fall back to text CLI input rather than terminating the agent.

Ollama connection failures should be surfaced with a clear `[Ollama]` error. Do not silently substitute a cloud API or a remote model.

Python execution failures should include the target exit code and captured stderr when available. Timeouts are treated as execution failures so an unresponsive target cannot block the harness indefinitely.

## Git hook

For local repositories that use the included `.git/hooks/pre-commit`, staged Python test files are passed through `heal_code.py` before the commit continues. If the repair changes a staged test, the hook re-stages the repaired file. A failed or unverifiable repair blocks the commit.

The `.git/hooks/` directory is managed by Git itself and is not normally cloned from a GitHub repository. For a fresh clone, recreate or install the hook locally before relying on it.

## Offline boundary

After the Python dependencies, Whisper model, Ollama application, and Ollama model have been installed, voice transcription, speech synthesis, code repair, and target execution are local operations. No cloud speech API, hosted coding API, or remote Git service is required for the core workflow.

## Agent orchestration guidance

When invoked by another agent, prefer the self-healing route for deterministic local execution problems. For interactive review flows, expose the proposal diff and wait for explicit approval before modifying the target. Keep the user-visible explanation concise, include the command that was attempted, the repair status, and whether the original backup remains available.
