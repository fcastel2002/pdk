# Design notes

These are the plans PDK was built from, kept as a record of the decisions and of what was found in the Ignition SDK along the way. They are written in Spanish, the author's working language; the user-facing documentation is in English.

| Note | What it covers |
|---|---|
| [F2-gateway-module.md](F2-gateway-module.md) | The `pdk-gateway` module: resource API, signatures and 409 conflicts, sandbox, build with `javac` |
| [F3-eyes.md](F3-eyes.md) | The "eyes": headless Chromium, joining the DOM with the gateway's evaluated tree, V/Q/L/C diagnostics |
| [F5-act-and-tags.md](F5-act-and-tags.md) | `--act` steps and simulated memory tags under `[default]PDK/` |
| [target-project.md](target-project.md) | Promoting finished views to a target project (`-Dpdk.target`) |

The notes cite sections of an internal roadmap ("roadmap §9.x") that is not published; each note stands on its own.
