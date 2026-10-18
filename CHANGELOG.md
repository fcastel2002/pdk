# Changelog

All notable changes to PDK are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
