# Benchmark

How well an agent builds real screens with PDK, and how well the validator catches mistakes.

- `briefs/`: seven screen requests with acceptance criteria (dashboard, form, coordinate panel, table, trend, alarms, navigation). In Claude Code, the `pdk-brief-runner` subagent builds one under `PDK/_bench/` in the sandbox and `pdk-reviewer` judges it against the criteria without seeing the runner's report.
- `tags/sandbox.json`: the simulated memory tags and alarms the briefs use (`uv run pdk tags apply bench/tags/sandbox.json`).
- `mutations.py`: the validator's mutation score: it breaks the fixture views in known ways and checks that every mutant is caught with a pointer and a hint (`uv run python -m bench.mutations`).
- `results/`: the runs made while building PDK (in Spanish, with the briefs' original Spanish names).
