---
name: perspective-views
description: Create or edit Ignition Perspective views (view.json) verified against the exact schemas of the installed version. Use for any request for a Perspective screen, panel, popup, template or component, or when touching a view.json.
---

# Perspective views with PDK

## Workflow
1. **Look it up before writing.** `pdk_catalog(type="...")` gives the real props (type, default, description), the component's events and, for containers, the `position` keys of their children. `pdk_catalog(topic="bindings" | "events" | "view")` gives the exact shapes. If a prop is not in the catalog, it does not exist: do not invent it.
2. **Start from a snippet** in `snippets/` (real views saved by the Designer and validated) and from [reference.md](reference.md).
3. **Write the view.json.** The hook validates every `view.json` you write or edit. If it reports problems, fix **all** of them before moving on. For in-memory JSON or a directory use `pdk_validate(view=...)` or `pdk_validate(path=...)`.
4. **Pass `project=`** (or work inside the project tree) to check embedded views, params and style classes.
5. **Iterate to 0 errors.** Every warning is either fixed or justified in the hand-off. The validator also reads code: **B010** expression syntax (`and`/`or`/`not`, unclosed parentheses or quotes; as a warning, whatever it cannot understand offline) and **E007** a script without a leading tab, which never runs at runtime. `focus="<code|component>"` expands the detail.
6. **Publish to the development gateway** (if the request is to see it running):
   - A new view goes in `work/<sandbox>/PDK/<Name>/view.json`; to edit an existing one, `pdk_pull(path="PDK/<Name>")` first.
   - `pdk_deploy(path=...)` validates, writes with the signature of the pull and returns the preview URL (`/pdk/<view path>`).
   - **G008** = someone changed it (almost always the Designer). Do not overwrite: read the diff, merge their changes from `view.gateway.json` and only then `pdk_deploy(..., resolved=true)`.
   - `pdk_promote` moves `PDK/X` to `X` and `pdk_delete` deletes it. Both are destructive: only when the user asked for it.
   - `pdk_promote(..., to_project="target")` sends it to the target (final) project: only with the user's explicit OK, and with no references to `PDK/` (promote embedded views and popups first).
7. **Watch it running** after every deploy: `pdk_inspect(path=...)` opens it in Chromium and returns the anomalies, each on the pointer of the component that has it:
   - **V** layout: V001 clipped text, V002 container overflow, V003 viewport overflow, V007 collapsed, V010 not rendered, V011 embedded view or repeater that does not load (check `props.path` and the params).
   - **Q** binding quality (points to the `propConfig`): Q001 `Bad_NotFound` (missing tag: fix the path), Q002 `Error_*` (failing script or expression), Q003 no value yet.
   - **L001** a script that failed on the gateway (points to the event) and **C001** a browser error.
   - **X002**: the page does not match the `view.json`, so deploy first.
   - **A** expectations (with `expect=`): A001 not met (includes what was measured), A002 malformed, A003 missing component.

   Fix, deploy and inspect again until it is clean. `focus=` brings the traceback or the boxes.

   **Verify it looks as requested with `expect=`**, not by eye. Before deploying, translate the request into expectations, one per `;` or line, and pass them on every inspect until they report `expect: N/N passed`. Components take the full name path from `root` (`root/Inputs/RowA/EntA`, not `root/EntA`), and `<a> below <b>` means `a` sits below `b`:
   - `root/Body below root/Header gap 8` (also `above`, `left-of`, `right-of`; `gap >= 8` or `gap 8..16` for a range)
   - `root/Log/Last inside root/Log` (inside its box or, on the scrolling axis, inside all of its content)
   - `root/Save aligned right root/Form` (`left|right|top|bottom|center-x|center-y`)
   - `root/Table width 100%` (`width|height|x|y`, `=` by default or `>= <= > <`; px or % of the parent's content box, inside its padding; 1px tolerance). `x`/`y` are measured from the inside of the parent (its padding box), like `position.x/y` in a coord container: `root/Diagram/Pump x 150`
   - `root/Title text "Pump 1"` (`text ~ "Pump"` contains; on an embedded view or repeater, the text its views render), `root/Alarm visible|hidden` (rendered, even if scrolled out of view)
   - `root/Events scrolls y` (the content overflows and that axis scrolls; without an axis, either), `root/Events/Last in-view` (fully in view: not scrolled away, clipped or outside the viewport)
   - `root/Title color #323232`, `bg`, `font-size 16`, `font-weight 700`

   **Verify behavior with `act=`**: steps that run before measuring, so `expect`, the qualities and failing scripts (L001) see the state afterwards. `click root/Save`, `type root/Name "P-2"` (confirms with Tab), `row root/Table 1` (first visible row), `wait 500`; for example `act="type root/Name \"P-2\""` with `expect="root/Save enabled"`, or `act="row root/Table 2"` with `expect="root/Detail text \"Pump A\""`. A step that cannot be done (disabled, no such row) stops there as **A004**; a malformed one, **A005**. If a step opens a popup, the inspect includes it: `expect="popup detail open"` (or `closed`) and its components as `popup:detail/root/Name` in `expect` and `query`; its anomalies come with that prefix, and steps reach them too (`act="click root/Open; type popup:detail/root/Value \"5\"; click popup:detail/root/Ok"`).
   **Simulated tags**: tag bindings are tested with memory tags under `[default]PDK/` (`pdk_tags`: `apply` a tag export JSON with `"valueSource": "memory"`, alarms included; `bench/tags/sandbox.json` has `Sandbox/Setpoint`, `Sandbox/Message` and `North Area/…` with alarms). In `act`, `tag Sandbox/Setpoint 42` changes the value and `tag Sandbox/Setpoint quality Bad_Stale` the quality (the view gets it through its subscription); in `expect`, `tag Sandbox/Message = Done` reads what a script wrote, `tag X quality Good` and `alarm "North Area" active|cleared`. Paths without `[provider]` hang from `PDK/`; writing outside it is G013 (A004 in a step).

   `query="root/Header, ia.display.label"` shows what was measured (box, text, color and font, background, padding, border, flex and gap, scroll) to write or tune expectations. `pdk_screenshot(path=..., component="root/Panel")` is for aesthetics; `viewport="1920x1080"` changes the size (1366x768 by default).
8. **Hand off** with the `pdk_validate` summary, the `pdk_inspect` summary (with its `expect:` line) and, if you published, the preview URL. The user sees the changes in the Designer with *Update Project* (Ctrl+Shift+U).

## Golden rules
- `position` uses the keys of the **parent container**: flex → `basis`, `grow`, `shrink`; coord → `x`, `y`, `width`, `height`.
- Bindings go in `propConfig["props.x"].binding`. The type is `expr`, never `expression`; transforms, on the other hand, do use `expression`.
- `script` actions take scope `"G"`. `nav`, `popup`, `dock`, `logout`… take scope `"C"`.
- `meta.name` is unique among siblings, without dots, `#` or `/`. The root is named `root`.
- View params: the default goes in `params` and the direction in `propConfig["params.x"].paramDirection` (`input`, `output`, `inout`).
- Styles in camelCase (`backgroundColor`), with CSS values as strings (`"12px"`, `"#2F5D7C"`).
- Style classes can also come from the stylesheet (`.psc-<name>` in `stylesheet/stylesheet.css`); `pdk pull stylesheet` / `pdk deploy` handle it in the sandbox, and `pdk_promote(path=".../_stylesheet", to_project="target")` sends it to the target (with explicit OK).
- The Designer omits default values; you do not need to write them.
