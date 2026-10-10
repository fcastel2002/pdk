# PDK for Ignition Perspective

A kit for an agent to build and fix Ignition Perspective views (`view.json`), verified against the exact installed schemas and a development gateway. Documentation: `docs/` (Sphinx, https://fcastel2002.github.io/pdk/).

## Commands
- `uv sync` · `uv run pdk doctor [--fix]` (see `README.md`) · `uv run pytest` · `uv run ruff check .`
- `uv run pdk schemas extract`: `schemas/<ver>/` from the installed `.modl` (not versioned).
- `uv run pdk validate <view.json|dir> [--project P]` · `uv run pdk catalog [type] [--prop P|--search S|--topic T]`
- `uv run pdk pull|deploy|promote|delete <path>`: sandbox views in `work/` with their signature; 409 → diff and `--resolved`. `promote --to-project target` copies to the target project (`-Dpdk.target`).
- `uv run pdk pull stylesheet` → `work/<sandbox>/_stylesheet/stylesheet.css`; `pdk deploy` of that folder sends it back (CSS checked, signature and 409 like views). It is edited in the sandbox; `promote work/<sandbox>/_stylesheet --to-project target` copies it to the target.
- `uv run pdk open|inspect|screenshot <path>`: the eyes (Chromium + the gateway's tree). `inspect --query <paths|types|*>` adds what was measured for those components and `--expect "<a> below <b>; <c> width 100%"` checks the layout (A001–A003); `--act "click <c>; type <c> \"x\"; row <table> N"` acts before measuring (A004–A005).
- `uv run pdk tags list|read|write|apply|delete|alarms`: simulated (memory) tags under the gateway's `[default]PDK/`; `apply bench/tags/sandbox.json` creates or resets the benchmark's. In `inspect`, `--act "tag Sandbox/Setpoint 42 [quality Bad_Stale]"` and `--expect "tag Sandbox/Setpoint = 42; alarm \"North Area\" active"` (A004, A001).
- `uv run pdk-mcp`: stdio MCP server with the CLI's `pdk_*` tools.
- `uv run pdk gateway build|install [--restart]|restart|health|sessions|logs|…`: the `gateway/` module (javac 17, REST).
- `uv run pytest -m gateway`: live tests (only under `PDK/_selftest/`).
- `uv run python -m bench.mutations`: the validator's mutation score.
- `uv run --group docs sphinx-build -W -b html docs docs/_build/html`: the documentation (published from `main` to GitHub Pages).

## Layout
`pdk/` (validate/, eyes/, catalog, sync, live, mcp_server, hook, gateway/) · `tests/` · `bench/` · `.claude/skills/perspective-views/` · `gateway/` (Java 17 module) · `docs/` (Sphinx) · `install.ps1`, `install.sh`.

## Rules
- Development only: never production gateways; write to the sandbox (`-Dpdk.sandbox`) and, only with promote and the user's OK, to the target (`-Dpdk.target`).
- Verification is part of the cycle: no ledgers, promotion states or evidence campaigns.
- Anomalies, not dumps: outputs of ~1,500 tokens at most, with `focus` for detail.
- Thin module, logic in Python (8.3 does not hot-reload modules).
- What cannot be validated is flagged (warning), not blocked.
- Every diagnostic carries a `pointer` (RFC 6901) and a `hint`; codes are stable.
- Never copy views from real projects into the repo; fixtures come from perspective-compiler v1.0 or are synthetic.
- Code, diagnostics and docs in English. A new diagnostic code goes in `docs/reference/diagnostics.rst` (a test checks it); a changed command or behavior, in the matching page of `docs/`.
