# Changelog

All notable changes to PDK are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/).

## [0.1.0] - Unreleased
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
- `--expect` reads an embedded view or a repeater through the text its views render, so `text ~` can check what a
  binding passed into it.
- `--expect` percentages of width and height are shares of the parent's content box (inside its border and padding,
  like CSS; a coordinate container's padding box), so `width 100%` holds for a child that fills a padded parent.
- Schema extraction on Windows: retry the final rename when a just-written file is still held, and never treat a leftover staging folder as a bundle version.
