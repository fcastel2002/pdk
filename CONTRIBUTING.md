# Contributing to PDK

Thanks for helping. PDK is maintained by one person, so small, focused pull requests with a test are the ones that land fastest.

## Before you start
- **Bugs**: open an issue with the PDK version (`git rev-parse --short HEAD`), Ignition and Perspective versions, the command you ran and the output. If it is about a view, reduce it to the smallest `view.json` that shows the problem.
- **Features and new diagnostics**: open an issue first so we can agree on the behavior and the code before you write it.
- **Never attach views, tags, screenshots or logs from a real project, a customer or a plant.** Reproduce the problem with a synthetic view in your sandbox.

## Development setup
```bash
git clone https://github.com/fcastel2002/pdk.git
cd pdk
uv sync
uv run pdk doctor --fix
```
Without a local Ignition install you can still work on most of the code: the tests that need the schema bundle are skipped.

## Checks
Every pull request must pass:
```bash
uv run ruff check .
uv run pytest
```
If you changed a command, a diagnostic or a behavior, update the matching page in `docs/` and check that it builds:
```bash
uv run --group docs sphinx-build -W -b html docs docs/_build/html
```
If you changed the validator, also run `uv run python -m bench.mutations` and keep the mutation score at 100%. Live tests (`uv run pytest -m gateway`) need the module installed on a development gateway; run them if you touched `gateway/`, `pdk/gateway/`, `pdk/sync.py`, `pdk/tags.py` or `pdk/eyes/`, and say so in the pull request.

## Conventions
- Code, identifiers, diagnostics and user docs in English.
- Every diagnostic has a stable code, a `pointer` (RFC 6901) and a `hint`. Never reuse or renumber an existing code.
- Outputs stay short (anomalies, not dumps; about 1,500 tokens at most), with `focus` for the detail.
- What PDK cannot validate is flagged as a warning, never blocked.
- The Java module stays thin: logic goes in Python, because Ignition 8.3 does not hot-reload modules.
- Test fixtures are views saved by the Designer or synthetic ones. No content from Inductive Automation (schemas, client bundle) goes into the repo: it is extracted locally.

## Contributor License Agreement
External contributions need a signed [CLA](CLA.md). The CLA bot asks for it on your first pull request; signing takes one comment. It lets the project keep its license options open while you keep the copyright of your work.

## Code of conduct
This project follows the [Code of Conduct](CODE_OF_CONDUCT.md).
