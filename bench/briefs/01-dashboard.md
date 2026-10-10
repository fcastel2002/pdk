# 01 · Reactive supervision dashboard

**Request**
> Build me a reactive supervision dashboard. At the top, four numeric inputs (A, B, C and limit), a text field with the operator's name, a notes area and a "Maintenance mode" checkbox. Below, a "Calculated readings" section with the sum, the average and A×B, and a load progress bar that shows the average against the limit. When maintenance mode is on, the three reading texts (sum, average and A×B) turn gray; the bar does not change.

**Acceptance criteria**
- `pdk_validate` with no errors or warnings.
- Visible texts: "Supervision dashboard", "Calculated readings", "Supervision states".
- Minimum components: 4 numeric entries, 1 text field, 1 text area, 1 checkbox, 1 progress bar.
- The readings are computed with `expr` or `property` bindings to the inputs, without scripts.
- The progress bar moves when A, B or C change, and the gray follows the checkbox (verified with `inspect --act`).

**Data**: no tags; everything lives in props and `custom`.
**Target**: sandbox → `PDK/_bench/Dashboard`.
**Source**: perspective-compiler's M25 "Dashboard" walkthrough (`examples/m21-reactive-dashboard.yaml`).
**Estimated manual time (Designer)**: _fill in_ min
