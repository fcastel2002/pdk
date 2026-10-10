## What and why
<!-- The problem this solves; link the issue (Fixes #...). -->

## How it was checked
- [ ] `uv run ruff check .`
- [ ] `uv run pytest`
- [ ] `uv run python -m bench.mutations` (if the validator changed)
- [ ] `uv run pytest -m gateway` on a development gateway (if the module, sync, tags or eyes changed)

## Checklist
- [ ] New or changed diagnostics keep stable codes, with `pointer` and `hint`.
- [ ] No views, tags or data from a real project, customer or plant.
- [ ] README/AGENTS.md/skill updated if a command or behavior changed.
