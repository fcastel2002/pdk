# 02 · Setpoint form

**Request**
> I need a form to enter a sandbox setpoint:
> - a required name field of up to 20 characters, with a message below it when it is invalid;
> - a numeric entry from 0 to 100;
> - a "Send setpoint" button, enabled only when the name is valid, that writes the value to the setpoint tag;
> - a "Send message" button that writes "Done" to the message tag.

**Acceptance criteria**
- `pdk_validate` with no errors or warnings.
- Visible texts: "Send setpoint", "Send message".
- Minimum components: 1 text field, 1 numeric entry, 2 buttons.
- The write actions are scripts with scope `G` on `events.component.onActionPerformed`.
- The button's enabled state comes from an `expr` binding that reads the name's validity.
- On click, the tag holds the value and the read-back matches: `--act` with `type` and `click`, `--expect "tag Sandbox/Setpoint = N; tag Sandbox/Message = Done"`.

**Data**: sandbox tags `[default]PDK/Sandbox/Setpoint` (Float8) and `[default]PDK/Sandbox/Message` (String), from `bench/tags/sandbox.json` (`uv run pdk tags apply bench/tags/sandbox.json`).
**Target**: sandbox → `PDK/_bench/Form`.
**Source**: M25 "Form" walkthrough (`examples/m22b-form.yaml`).
**Estimated manual time (Designer)**: _fill in_ min
