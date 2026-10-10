# 05 · Flow trend

**Request**
> Build me a flow trend: a time series chart with the "Flow" series, which comes from a param, and a fixed "Target" line at 15 m3/h, both on the same plot. Axis from 0 to 100 labeled "m3/h", visible legend and 300 px tall.

**Acceptance criteria**
- `pdk_validate` with no errors or warnings.
- There is an `ia.chart.timeseries` component.
- `props.series[0].data` is bound to a param with `{time, value}` points.
- The target series is literal.
- Both trends reference the same axis (`axis`) and the same plot.
- The legend shows "Flow" and "Target" (not "value"); the param still arrives as `{time, value}`.
- The chart has an explicit height, so it does not collapse (HMI010 clean).

**Data**: `params.flowPoints` (list of `{time: epoch ms, value}`).
**Target**: sandbox → `PDK/_bench/Trend`.
**Source**: M25 "Trend" walkthrough (`examples/m23-trend.yaml`).
**Estimated manual time (Designer)**: _fill in_ min
