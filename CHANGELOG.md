# Changelog

All notable changes to PDK are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.5.0] - 2026-10-19

The final community edition: PDK is frozen at this version and the repository is archived. It stays under the
Apache License 2.0 and gets no more changes or fixes, security fixes included. The maintained edition is commercial
(see the README).

### Changed
- On the day's first command (and to the agent when the MCP server starts), PDK says once that this is the final
  community edition and where the maintained one is. It rides on the existing daily check: no new network call, and
  `PDK_UPDATE_CHECK=0` still turns it off.

### Added
- Agent sessions: `pdk-mcp --agent-session <folder>` enforces the rules of a host such as PDK Agent in the server,
  for every tool call: normal mode reads, looks at sandbox views and edits props of the view open in the Designer;
  Ultra waits for an approved plan and then writes only the targets it names. The state comes from the host, signed
  (`PDK_AGENT_KEY`), and an approval expires unless the host renews it; without a valid state only reading and
  looking run. New codes T001-T006.
- In an agent session, `pdk_work` (the agent's files, only PDK's resource files under the work folder, a written
  view validated at once) and `pdk_plan` (the plan as data, with what the gateway says about each target).
- `pdk_guide`: the perspective-views skill over MCP, for clients that do not load skills.

## [0.4.1] - 2026-10-19

### Security
- `pdk promote` without `--to-project` could write into the target project from a folder that belonged to it (one
  left by an earlier promote): every promote now starts from the sandbox, like style classes and scripts already
  did.
- `act` (in `pdk inspect` and `pdk screenshot`) runs a view's own event scripts on the gateway, so it now works only
  on sandbox views; a view promoted to the target is looked at without it.

### Fixed
- The eyes close the Perspective sessions they open on promoted views too: the module refused to close sessions
  outside the sandbox, so every look at a target view left one open until it timed out (on Maker, which caps them,
  that ran out of sessions). Each page is closed in the project it was opened in. Needs the module reinstalled
  (`pdk gateway install --restart`).
- The MCP server's browser no longer closes for being idle while a call is running, and Playwright errors come back
  as eyes errors (with their hint) instead of a traceback; a screenshot of a component with no size fails at once
  instead of after 30 s.
- A collapsed component is reported once, as its V007: a scroll or an overflow of the component itself (its
  content poking out of a zero-height box) no longer adds a V014 or V002.
- `pdk inspect` with a step it cannot read (A005) stops before opening the view, as `pdk screenshot` does, instead
  of checking the expectations on the page before any step.
- Validating a directory builds each project once (it rebuilt the project's reference graph for every view), and
  `focus` on an info code across a directory shows those views.
- `pdk validate` and the hook check a view pulled to `work/<project>/` against that project, like `pdk deploy`
  does, so a missing embedded view (R010) shows before the deploy refuses it.
- A deploy over a style class or script that already exists and was never pulled can be resolved with
  `--resolved`, as views can.
- A change from `1` to `true` (or `0` to `false`) inside a view is deployed: the comparison with the pulled
  version told them apart only at the top level.
- Promote leaves PDK's scratch folders (`PDK/_bench/`, `PDK/_selftest/`) unless `--to` names the destination.
- R013 no longer flags an embedded view whose whole `params` object is bound; BP006 no longer takes a component's
  write to `self.view.custom.x` for its own property, and checks view-level change scripts (`self.params.x`).
- Module errors map to the right codes: `BUSY` and `INTERRUPTED` are G012 (busy, retry), a missing history
  provider is G007, deleting a folder that is not empty is G009; a test keeps the map, the module and the REST
  reference in step.
- The MCP server refreshes only the skill, the subagents and the CLAUDE.md section of a folder after an upgrade:
  it rewrote the `pdk` entry of `.mcp.json` (dropping its `env`) and the hook. `pdk init` keeps that `env` and the
  hook's timeout.
- A folder set up with `pdk init` from a clone keeps its pulled views in its own `work/` (they went to the clone's),
  and `pdk_refs` writes its inventory under the work folder (`PDK_WORK_DIR`).
- The sample memory tags the tutorial, the README and the skill use ship with the package: `pdk tags apply sample`
  (`bench/tags/sandbox.json` was only in a clone).
- What the target project takes is stated the same way everywhere (SECURITY.md, the README, the docs and the G006
  hint): views, style classes and scripts outside `PDK/`, the stylesheet, and the eyes' preview pages. The skill
  and the server's instructions say promote copies, and give the order that works for a view that embeds `PDK/`
  views; the B011 hint no longer suggests `tag()`, which BP001 flags.
- The setup for other MCP clients (Codex, Gemini CLI, VS Code, Cursor) works without a clone: `pdk-mcp` with
  `PDK_WORK_DIR`, with each client's format in the agents guide.

## [0.4.0] - 2026-10-19

### Added
- Integrator best practices for bindings and performance (BP001-BP018): `tag()` in bindings, script transforms
  that should be map or expression transforms or that block, binding loops and mirrored bidirectional bindings,
  change scripts without an origin guard, bound input params, fast polling, clocks and runScript, pass-through
  expressions, a tag bound many times, UDTs dropped by binding, deep embedded views, queries and heavy components in
  repeated views, literal colors, repeated inline styles, bindings that move the layout. Errors and warnings only
  for what breaks or costs; the rest is information.
- `pdk refs tag|view` and the `pdk_refs` MCP tool: where a tag or a view is used in a project, with indirect tag
  paths expanded through the params the view's callers pass. `pdk inventory` (Markdown, CSV, JSON): every view
  with its routes, params, callers, tags and write points, named queries, and the views nothing opens.
- `pdk validate --tags` (`tags=true` in `pdk_validate`, and on every deploy as warnings): tag paths checked on the
  gateway, indirect ones expanded through the callers. New codes R020 (tag not found) and R021 (paths known only
  at runtime).
- The eyes open views promoted to the target: `pdk inspect work/<target>/<view>` adds a `/pdk/` preview page to the
  target's page configuration, the only change the module accepts there; `promote --to-project` says how to look.

## [0.3.0] - 2026-10-14

### Added
- PDK installs as a command, with no clone, no git and no Python of your own: `uv tool install pdk-perspective`
  (the installers do it). `pdk` and `pdk-mcp` work from any folder; the schema bundle and the module build live
  under `~/.pdk/` (`PDK_HOME`), pulled views in `work/` of the current folder. A clone works as before.
- `pdk init [folder]` sets up a folder for Claude Code: the `perspective-views` skill, the `pdk-reviewer` and
  `pdk-log-reader` subagents, the `pdk` MCP server in `.mcp.json`, the validation hook and a PDK section in
  `CLAUDE.md`, merged with what is there. The MCP server refreshes them after an upgrade.
- PDK says when a newer release is out: once a day it asks GitHub's public API, then tells you on the next command,
  in `pdk doctor` (check `version`) and through the MCP server, so the agent mentions it at the start of the
  session. `PDK_UPDATE_CHECK=0` or `update_check = false` turns it off.
- `pdk --version`.
- Releases: a draft release follows `[Unreleased]` with a suggested version; publishing it tests, attaches the wheel,
  publishes to PyPI and rolls the changelog.

### Changed
- The version comes from the git tag (hatch-vcs); the build backend is hatchling.
- Hints name the command as it runs on the machine (`pdk ...` installed, `uv run pdk ...` in a clone), and point to
  `pdk doctor --fix` for Chromium.

## [0.2.0] - 2026-10-14

### Added
- V014: a container that scrolls without a `scrolls` expectation asking for it (an error inside another scroll
  along the same axis or for under 24px of content), or that shows a scrollbar with nothing to scroll. V002 can
  no longer be silenced with `style.overflow`.
- `--expect` reads properties as the gateway evaluated them (`root/Bar prop value >= 40`, `view prop custom.x = 3`)
  and where a navigation went (`page /detail`, `page shows PDK/Detail`).
- `pdk screenshot --act` (and `act` in `pdk_screenshot`) runs steps before capturing.
- V012: a flex repeater whose instances are bigger than it on the axis their number does not explain.
- `--act` steps `select` (dropdown option), `slide` (slider), `key`, `hover`, `double-click` and `scroll`.
- `--viewport` presets (`desktop`, `tablet`, `phone`, `wide`); `inspect` takes several, comma-separated, with one
  report each.
- V013: text with low contrast against what is behind it (WCAG AA), on text components whose colors the view
  chose (the theme's defaults and text drawn inside charts are not judged).
- `pdk screenshot --compare before.png` (and `compare` in `pdk_screenshot`): the share of pixels that changed, the
  components where, and a diff image.
- `pdk schemas functions` saves the gateway's expression functions and `system.*` names next to the bundle; with
  them, B011 (unknown expression function) and E008 (unknown `system.*` name), with "did you mean".
- E009: `pdk deploy` compiles every script with the gateway's Jython before writing (`pdk validate --compile`,
  `compile` in `pdk_validate`); module routes `GET /functions` and `POST /scripts/compile`.
- Style classes and project scripts are pulled, deployed, promoted and deleted like views
  (`style-classes/PDK/X`, `scripts/PDK/X`; `work/<sandbox>/_style-classes/`, `_scripts/`), checked before every
  write; the target project takes them through promote.
- R016: a query binding names a named query the project does not have.
- `pdk pages list|add|remove` (and `pdk_pages`): the routes of the sandbox's page configuration, so a `nav` to a
  page has somewhere to go.
- When a step navigates to a URL without a route, `inspect` says so instead of failing to open the view.
- Simulated UDTs: definitions under `[default]_types_/PDK/` (memory members, bindings only to parameters) and
  instances under `PDK/`.
- Simulated history: `historyEnabled` on `PDK/` memory tags, and `pdk tags history <path> <values> --every 1m`
  (`history` in `pdk_tags`; module routes `GET`/`POST /history`) stores synthetic points through
  `system.tag.storeTagHistory`, so trends and tag-history bindings have data.

### Changed
- Closing PDK's browser also closes its Perspective sessions on the gateway (`DELETE /sessions`), so they no longer
  hold a session slot until they time out.
- When an `--act` step navigates, `inspect` reports the view the page shows afterwards instead of X002.
- `pdk_screenshot` is declared destructive in MCP, like `pdk_inspect`, since `act` runs the view's scripts.

## [0.1.0] - 2026-10-10
First public release.

### Added
- Offline validator for `view.json` against the schema bundle extracted from the installed Perspective (props, bindings, events, expressions, scripts, references), with stable codes, JSON pointers and hints.
- Catalog of components, props, bindings and events from the same bundle (`pdk catalog`).
- Gateway module (Java 17) with a loopback-only, token-protected REST API: signed pull, deploy, promote and delete of views and the stylesheet, sandbox and target projects, sessions, logs and simulated memory tags.
- Eyes: `pdk inspect`, `pdk open` and `pdk screenshot` render the view in headless Chromium and report layout, binding-quality, script and browser anomalies on the component that has them; `--expect` checks layout and tags, `--act` clicks, types, selects rows and sets tags before measuring.
- MCP server (`pdk-mcp`), Claude Code skill, validation hook and subagents.
- `pdk doctor` and the `install.ps1` / `install.sh` installers.
- Benchmark briefs and the validator's mutation score (`bench/`).
- Documentation site built with Sphinx (`docs/`): introduction, installation, tutorial, user guide, architecture, troubleshooting and the reference of the CLI, diagnostic codes, MCP tools, REST API and Python API.

### Fixed
- `pdk screenshot` without `--out` writes to `work/_screenshots/<view>[__<component>].png`, not to the current
  directory.
- R010's hint looks for the missing view in its own folder first; a near name elsewhere (another run's copy) is
  offered after "create the view first", not as the fix.
- `--expect text` reads an embedded view or a repeater through the text its views render, and a text field or text
  area through its value (it read `''` before).
- `--expect` percentages of width and height are shares of the parent's content box (inside its border and padding,
  like CSS; a coordinate container's padding box), so `width 100%` holds for a child that fills a padded parent.
- Schema extraction on Windows: retry the final rename when a just-written file is still held, and never treat a leftover staging folder as a bundle version.
