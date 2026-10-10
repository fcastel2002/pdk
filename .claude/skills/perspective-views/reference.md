# Reference: view.json idioms

Shapes observed in real Perspective 3.3.x views. When in doubt, `pdk_catalog` rules: it comes from the schemas of the installed version.

## Envelope
```json
{
  "custom": {},
  "params": {"motor": "P-101"},
  "propConfig": {"params.motor": {"paramDirection": "input", "persistent": true}},
  "props": {"defaultSize": {"width": 800, "height": 600}},
  "root": {
    "type": "ia.container.flex",
    "meta": {"name": "root"},
    "props": {"direction": "column"},
    "children": []
  }
}
```

Keys per level:
- **View:** `root`, `props`, `custom`, `params`, `propConfig`, `events` (only `system.onStartup` and `onShutdown`), `permissions`.
- **Component:** `type`, `meta`, `props`, `position`, `custom`, `propConfig`, `events`, `scripts`, `children` (the latter only in containers).

## Layout
- **Flex** (`ia.container.flex`):
  - Props: `direction` (`row` by default, or `column`), `justify` (not `justifyContent`), `alignItems`, `wrap`.
  - Children: `position.basis` (`"32px"`, `"auto"`, `"100%"`), `grow` and `shrink`.
  - Table, trend and menu tree need `basis` or `grow`; otherwise they collapse.
- **Coordinate** (`ia.container.coord`):
  - Children with `position` `{x, y, width, height}` in px.
  - With `props.mode: "percent"`, values go from 0 to 1 (`snippets/coordinate-panel.view.json`).
- **Breakpoint, column, tab and split:** `pdk_catalog(type=...)` shows the position keys of their children.

## Bindings (`propConfig`)
| Type | `config` | Example |
|---|---|---|
| `tag` | `tagPath`, `mode` (`direct`, `indirect`, `expression`), `bidirectional`, `references` | `[default]Area1/Status` |
| `expr` | `expression` | `"{view.params.speed} * 2"` |
| `property` | `path`, `bidirectional` | `"view.params.rows"`, `"../Slider.props.value"`, `"/root/Table.props.selection.data"` |
| `expr-struct` | `struct` (keys → expressions), `waitOnAll` | |
| `query` | `queryPath`, `parameters`, `returnFormat` | |
| `tag-history` | `tags`, `dateRange`, `returnFormat` | |

Property paths:
- `this.props.x`
- `parent.custom.y`
- `view.params.z`
- `view.custom.w` (the view's own state: inputs with a bidirectional binding and expressions that read them)
- `session.props.locale`
- `page.props.path`
- `/root/Panel/Label.props.text` (absolute)
- `../Sibling.props.value` (relative; `..` is the parent container)

A path that does not resolve gives a bad quality at runtime.

Expressions (`expr`, `expression` transforms):
- Logical: `&&`, `||`, `!`. **There is no `and`, `or` or `not`**: they give a Syntax Error, which shows up at runtime as Q002.
- Comparison: `=` (or `==`), `!=`, `<`, `<=`, `>`, `>=`. Arithmetic: `+ - * / %`; `+` also concatenates text.
- Conditional: `if(cond, ifTrue, ifFalse)`; nulls with `isNull(x)` and `coalesce(a, b)`; length with `len(x)` (text or list).
- References in braces: `{view.custom.a} + {view.custom.b}`, `{/root/Table.props.selection.data[0].name}`.

Transforms (`binding.transforms`, in order; `{value}` is the incoming value):
- `{"type": "expression", "expression": "{value} > 50"}`
- `{"type": "map", "inputType": "scalar", "outputType": "scalar", "mappings": [{"input": true, "output": "RUNNING"}], "fallback": "UNKNOWN"}`
- `{"type": "format", "formatType": "numeric", "formatValue": "number"}`
- `{"type": "script", "code": "\treturn value * 2"}`: every line of the body starts with a tab (Ignition wraps it in a function); without it, Q002 "Parse Error in script transform".

Examples: `snippets/tag-transforms-script.view.json`, `snippets/table.view.json` (expr with absolute paths) and `snippets/template-with-params.view.json`.

## Events and actions
```json
"events": {
  "component": {"onActionPerformed": {"type": "script", "scope": "G", "config": {"script": "\tsystem.tag.writeBlocking(['[default]X/Cmd'], [True])"}}},
  "dom": {"onClick": {"type": "nav", "scope": "C", "config": {"page": "/alarms"}}}
}
```
- **Buttons:** use `events.component.onActionPerformed`. For everything else, DOM events (`onClick`, `onDoubleClick`…) or the component events the catalog lists.
- **Navigation:** `nav` with `{"page": "/path"}` changes the page; `{"view": "Folder/View", "params": {}}` changes the view.
- **Popups:** `popup` with `{"type": "open", "id": "detail", "viewPath": "Popups/Equipment", "viewParams": {}}`; to close it, `{"type": "close", "id": "detail"}`. See `snippets/navigation-actions.view.json`.
- **Scripts:** the code starts with a tab (`"\t..."`). It runs on the gateway (scope `"G"`), but `self` is the component in that view: `self.view.custom.x` and `self.view.params.x` read the view's state, `self.getSibling("Name").props.value` a sibling and `self.parent` the container. To write a tag with what the user entered: `system.tag.writeBlocking(['[default]PDK/Sandbox/Setpoint'], [self.view.custom.setpoint])`.

## Composition
- **Embedded view:** `ia.display.view` with `props.path` and `props.params`.
- **Repeater:** `ia.display.flex-repeater` with `props.path` and `props.instances`, a list of objects with each instance's params, or a binding to that list. See `snippets/embedded-and-repeater.view.json`.
- **Deploy order:** deploy embedded views, popups and nav targets first; otherwise `pdk_validate project=` gives R010 and the inspect, V011.
- **Params of the target view:** the default goes in `params` and the direction in `propConfig["params.x"].paramDirection`. `output` params are read from the view that embeds it; they are not passed in.

## Traps seen in real projects
- A binding with type `"expression"`: the gateway rejects it ("missing binding type"); it is `expr`.
- Flex with `justifyContent`: the prop is `justify`.
- In table columns, `align` is **vertical** (`top`, `center`, `bottom`); horizontal alignment uses `justify`.
- View paths without a folder (`"AlarmTable"` instead of `"Page/AlarmTable"`) give "view not found".
- Params passed to a view that does not declare them are silently ignored.
- `onSelect` on an alarm table is the DOM text-selection event, not row selection. That table has no row event: to navigate, use a button. `ia.display.table` does have `onRowClick`.
- Alarm tables show several columns by default (`activeTime`, `source`, `name`…) even if you do not configure them, and `order` alone does not reorder them: set `enabled` explicitly on **all** of `columns.active` (`true` with its `order` on the ones you want, `false` on the rest). To show the path and the priority **first**: `displayPath` order 0 and `priority` order 1, both `enabled: true`, and the rest with a higher `order` or `enabled: false`; confirm it with a screenshot.
- The status table also lists cleared-but-unacknowledged alarms by default (`filters.active.states.clearUnacked: true`): for "active only", set it to `false`.
- Filtering alarms by area: `displayPath` (`"North Area/*"`) in `filters.active.conditions` (status) and in `filter.conditions` (journal); it falls back to the source path when the alarm has no displayPath. `source` uses the qualified path, `prov:default:/tag:Folder/*`, not `[default]Folder/*`; do not add it if the request is by area: conditions are combined and leave out alarms of the area that have another source.
- Trend (`ia.chart.timeseries`): the legend shows the **value key** in `series[].data` (the one in `trends[].columns[].key`), not `series[].name`. If the param brings `{time, value}`, the series shows up as "value": to make it read "Flow", rename the key in the binding and use it in the trend:
  ```json
  "transforms": [{"type": "script", "code": "	return [{'time': p['time'], 'Flow': p['value']} for p in value]"}]
  ```
  with `trends[].columns[].key = "Flow"`. A literal series is named the same way: `{"time": ..., "Target": 15}`. Confirm it in the screenshot.
- Built-in images: `/system/images/Builtin/icons/48/about.png` is a verified path. For others, check *Image Management* on the gateway; do not invent names.
- Folder or view names reserved on Windows (`Aux`, `Con`, `Nul`, `Prn`, `Com1`…): Ignition stores them encoded on disk (`%41%75%78`). PDK resolves them, but `work/` and other tools do not: avoid them.
- The `hmi-*` classes in the snippets are the design system of perspective-compiler and only exist in projects that import them.
