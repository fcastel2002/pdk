# 06 · North Area alarm screen

**Request**
> I want an alarm screen for the North Area. At the top, an active alarm table filtered by `North Area/*`, showing the path and the priority first. Below, a real-time journal of the last hour, on the `Sample_Alarm_Journal_Profile` profile, with a 15-row pager.

**Acceptance criteria**
- `pdk_validate` with no errors or warnings.
- Minimum components: 1 `ia.display.alarmstatustable` and 1 `ia.display.alarmjournaltable`.
- Both tables filter by `North Area/*`.
- The journal is in realtime mode with a 1-hour interval.
- The alarm table does not use `onSelect` (HMI011): it has no row event.
- Neither table collapses in the flex.

**Data**: journal profile `Sample_Alarm_Journal_Profile`. Real alarms from `bench/tags/sandbox.json` (displayPath `North Area/Tank 1` and `North Area/Pump 1`): `--act "tag \"North Area/Tank 1 Level\" 95"` activates one and `alarm "North Area" active` confirms it.
**Target**: sandbox → `PDK/_bench/Alarms`.
**Source**: M25 "Alarms" walkthrough (`examples/m24-alarms.yaml`).
**Estimated manual time (Designer)**: _fill in_ min
