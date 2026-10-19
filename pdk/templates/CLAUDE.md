# Ignition Perspective with PDK

This folder builds and fixes Ignition Perspective views (`view.json`) with PDK, verified against the exact schemas of
the installed Perspective and a development gateway. Docs: https://fcastel2002.github.io/pdk/

- Use the skill `perspective-views` for any view, popup, template or component: it has the workflow and idioms.
- The `pdk` MCP server (`.mcp.json`) gives `pdk_validate`, `pdk_catalog`, `pdk_pull`, `pdk_deploy`, `pdk_promote` and
  `pdk_delete` (the last two destructive), `pdk_tags`, `pdk_pages`, `pdk_refs` (where a tag or view is used), and
  the eyes `pdk_open`, `pdk_inspect` and `pdk_screenshot`. The same commands exist in the terminal as `pdk <command>` (`pdk --help`).
- Writes go only to the sandbox project (`pdk gateway health` names it), with your work under `PDK/`. Promote to the
  target project (`pdk_promote` with `to_project="target"`) only with the user's OK. Never use a production gateway.
- Pulled views live in `work/<sandbox>/`. Every `view.json` you write is validated by a hook; fix what it reports.
- Subagents: `pdk-reviewer` (a deployed view against acceptance criteria, with inspect and screenshots) and
  `pdk-log-reader` (summary of the gateway's logs).
- If something fails, `pdk doctor` says what is missing. When PDK says a newer version is out, tell the user.
