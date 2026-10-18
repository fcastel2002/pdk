# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md

## Claude Code
- The `pdk` MCP server (`.mcp.json`) exposes `pdk_validate`, `pdk_catalog`, `pdk_pull`, `pdk_deploy`, `pdk_promote` and `pdk_delete` (the last two destructive), `pdk_tags` (simulated tags under `PDK/`), `pdk_refs` (where a tag or view is used in a project), and the eyes `pdk_open`, `pdk_inspect` and `pdk_screenshot`; it loads when the session starts. Its Chromium closes by itself after 2 minutes idle (`PDK_EYES_IDLE_S`): an open page holds a Perspective session, and some editions limit them. While it runs it locks `.venv/Scripts/pdk-mcp.exe` on Windows: after changing dependencies, `uv sync` fails until the session restarts.
- Writes to the gateway go only to the sandbox (the project named by `-Dpdk.sandbox`, reported by `uv run pdk gateway health`), with the agent's work under `PDK/`. Changing the Java module means reinstalling it and restarting the gateway: ask for an OK first.
- PostToolUse hook (`.claude/settings.json`): every `view.json` you write or edit is validated automatically and problems arrive on stderr. Fix them before moving on.
- Haiku subagents (`.claude/agents/`): `pdk-mechanic` (bounded changes, ends with pytest+ruff green), `pdk-auditor` (read-only comparisons), `pdk-bundle-reader` (one component of the JS bundle at a time), `pdk-sdk-reader` (one API question about the jars, with javap), `pdk-log-reader` (summary of gateway logs), `pdk-reviewer` (a deployed view against acceptance criteria, only with inspect and screenshots) and `pdk-brief-runner` (runs a brief from `bench/briefs/` with the full cycle). Give them closed tasks; design, new rules and everything that writes to the gateway stay in the main session. The only exception: `pdk-brief-runner` deploys and deletes, only under `PDK/_bench/` in the sandbox. They are built to stay under ~100k input tokens.
- Skill `perspective-views`: workflow and idioms for creating or editing views.
- Fixtures in `tests/fixtures/compiler-v1/` (Perspective 3.3.8); the local bundle comes from the install (`pdk schemas extract`).
- The local gateway's projects (`$IGNITION_HOME/data/projects`) are read-only for PDK; so are tags, except `[default]PDK/` (G013).
- On Maker Edition gateways the tag count is capped: past the limit, new tags stay `Bad_LicenseExceeded` and refuse writes until tags are deleted and the gateway restarts.
- If something in the environment fails, `uv run pdk doctor` says what is missing. Per-machine notes go in `CLAUDE.local.md` (not versioned).
