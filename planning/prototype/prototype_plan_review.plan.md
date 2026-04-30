---
name: prototype plan review
overview: Review the existing prototype plan and define the small addendum needed before Opus 4.7 extra high builds the app. The existing plan is detailed enough to use as the main implementation plan, but the handoff should explicitly add latest-version verification and a more complete README requirement.
todos:
  - id: use-existing-plan
    content: Use the existing detailed prototype plan as the implementation source of truth.
    status: pending
  - id: add-version-check
    content: Add latest stable version verification before dependency files are finalized.
    status: pending
  - id: expand-readme
    content: Require the README to include full install, test, run, API key, and troubleshooting instructions.
    status: pending
  - id: execute-checkpoints
    content: Have Opus build through the existing checkpoint sequence with fake-provider tests before real-provider use.
    status: pending
isProject: false
---

# Prototype Plan Review

## Decision

Do not create a full replacement plan. Use [planning/prototype/urdu_audio_article_prototype_detailed_plan.md](planning/prototype/urdu_audio_article_prototype_detailed_plan.md) as the primary build plan for Opus 4.7 extra high.

It is comprehensive enough on the core prototype: Python-first local app, Streamlit UI, CLI, modular stages, fake providers, artifacts, manifests, cache, budget guardrails, OpenAI provider abstraction, tests, acceptance criteria, and checkpointed implementation. It also aligns with the source instructions in [planning/prototype/cursor_urdu_article_prototype_opus_instructions_v1(2).txt](planning/prototype/cursor_urdu_article_prototype_opus_instructions_v1(2).txt) and the context summary in [planning/prototype/urdu_audio_prototype_chat_context_summary_for_next_gpt55.txt](planning/prototype/urdu_audio_prototype_chat_context_summary_for_next_gpt55.txt).

## Required Addendum Before Build

Add these instructions to the Opus handoff before implementation starts:

- Treat the existing detailed plan as source of truth unless it conflicts with the prototype instruction file.
- Use latest stable versions, not prerelease/alpha versions, and verify them at implementation time from official sources or PyPI before writing dependency files.
- Prefer `pyproject.toml` as the canonical dependency/project metadata file, with `requirements.txt` optionally generated or kept as a simple install path if faster for the prototype.
- Use Python `3.14.4` if all selected libraries install cleanly; if a dependency incompatibility blocks progress, use the newest mutually compatible stable Python release and document that in `README.md`.
- Current version snapshot to verify: OpenAI SDK `2.32.0`, Streamlit `1.56.0`, Pydantic `2.13.3`, pydantic-settings `2.14.0`, pytest `9.0.3`, Typer `0.25.0`, Rich `15.0.0`, tiktoken `0.12.0`, FFmpeg `8.1`.
- Before implementing real OpenAI calls, verify current OpenAI model IDs, transcription endpoints, text generation endpoints, upload/file-size limits, response shapes, and pricing. Do not trust stale model/pricing assumptions.
- Keep fake-provider mode fully functional without `OPENAI_API_KEY`.
- Never call paid APIs in tests, setup checks, or default smoke tests.
- Make accepted audio input file types configurable in settings. Default to `.mp3` because the initial user-provided files will be MP3, but do not hardcode the pipeline to MP3-only. The chunker, UI upload widget, CLI validation, artifact metadata, and README should all use the configured accepted extensions.

## README Must Be Built As A Deliverable

At the final checkpoint, `README.md` must include step-by-step instructions for:

- What the prototype does and does not do yet.
- Which audio file types are accepted by default, that `.mp3` is the expected initial format, and how to change the accepted extensions in configuration.
- Required local tools: Python, pip/venv, FFmpeg/ffprobe, Git if needed, and platform-specific install commands for macOS, Windows, and Linux where practical.
- How to verify installed versions with commands such as `python --version`, `ffmpeg -version`, and `ffprobe -version`.
- How to create and activate a virtual environment.
- How to install Python dependencies.
- How to copy `.env.example` to `.env`.
- How to run the app in fake-provider mode without an API key.
- How to run the full test suite and targeted tests.
- How to launch Streamlit.
- How to use every CLI command.
- How to obtain an OpenAI API key from `https://platform.openai.com`, add billing/credits if required, create a project key, copy it once, and place it in `.env` as `OPENAI_API_KEY`.
- Where to set `PIPELINE_PROVIDER_MODE=fake` versus `PIPELINE_PROVIDER_MODE=real`.
- How cost estimation, confirmation, budget target, and `$60` hard cap work.
- Where outputs are saved under `runs/`.
- How to resume from prior artifacts at each stage.
- How to change model IDs, pricing config, prompt files, chunk length, overlap, and output root.
- Common troubleshooting: missing FFmpeg, missing API key, wrong artifact uploaded, chunk too large, missing model pricing, budget cap exceeded, dependency install failure, and OpenAI authentication/rate-limit errors.

## Build Flow For Opus

Use the existing seven checkpoints, with one small adjustment: after the skeleton is created, immediately verify dependency compatibility before implementing the rest of the app.

1. Create skeleton, dependency files, settings, schemas, fake providers, and initial tests.
2. Verify latest stable dependency versions install cleanly in a fresh virtualenv.
3. Implement artifact store, validation, exporter, budget guard, cache, and chunker.
4. Implement transcription with fake provider first, then real OpenAI provider behind config.
5. Implement reconciliation, translation, and article stages.
6. Implement CLI commands.
7. Implement Streamlit UI.
8. Run end-to-end fake-provider integration test.
9. Complete `README.md` with the full setup/run/API-key instructions above.

## Acceptance Gate

The app is not complete until `python -m pytest` passes without paid API calls and the README is detailed enough for a fresh machine setup from scratch.