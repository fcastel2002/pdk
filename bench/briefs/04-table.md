# 04 · Pump table

**Request**
> I want a read-only pump table fed by a `rows` param, with:
> - typed columns: name (text), flow (number with one decimal) and running (boolean);
> - pager, filter and descending sort by flow;
> - single selection.
>
> Below it, a label that shows the name of the selected pump, or nothing when there is no selection.

**Acceptance criteria**
- `pdk_validate` with no errors or warnings.
- Minimum components: 1 table and 1 label.
- `props.data` is bound to `view.params.rows`, and the param has a default with at least 2 rows.
- The table has `position.basis` or `grow`: it does not collapse (HMI010 clean).
- The label uses an `expr` binding on `/root/<Table>.props.selection.data` and does not fail without a selection.
- The columns' horizontal alignment uses `justify`; `align` is vertical.

**Data**: `params.rows` with rows `{name, flow, running}`.
**Target**: sandbox → `PDK/_bench/Table`.
**Source**: M25 "Table" walkthrough (`examples/m23-table.yaml`).
**Estimated manual time (Designer)**: _fill in_ min
