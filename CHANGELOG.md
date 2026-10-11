# Changelog

All notable changes to PDK are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
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
