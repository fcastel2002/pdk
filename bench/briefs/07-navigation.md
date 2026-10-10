# 07 · Navigation and composition

**Request**
> Build me a navigation screen with:
> - a menu tree with "Home" (`/`) and "Operation > Alarms" (`/alarms`);
> - a "View equipment" button that navigates to the view `PDK/_bench/Auxiliary/Equipment` with `name = P-101`;
> - an "Open detail" button that opens the popup `PDK/_bench/Auxiliary/EquipmentPopup` (id `equipment-detail`, not modal, with a close icon, params `name` and `running`);
> - a "Close detail" button that closes that popup.
>
> Below, an embedded summary of `PDK/_bench/Auxiliary/Summary` that receives the area as a param and the name from a text field, and two repeaters of `PDK/_bench/Auxiliary/Pump`: one fed by the `pumps` param and another with two literal pumps.

**Acceptance criteria**
- `pdk_validate project=<sandbox>` with no errors: the target views exist and the params passed are declared.
- Visible texts: "View equipment", "Open detail", "Close detail".
- Minimum components: 1 menu tree, 3 buttons, 1 text field, 1 embedded view, 2 flex repeaters.
- The actions are `nav` and `popup` (scope `C`) on `onActionPerformed`. The popup opens and closes with the same `id`.
- The name reaches the summary through a `property` binding from the text field.

**Data**: auxiliary views `PDK/_bench/Auxiliary/Equipment`, `PDK/_bench/Auxiliary/EquipmentPopup`, `PDK/_bench/Auxiliary/Summary` and `PDK/_bench/Auxiliary/Pump`. If they do not exist, create them first in the sandbox, with the params the screen uses (Equipment: `name`; EquipmentPopup: `name`, `running`; Summary: `area`, `name`; Pump: `name`, `running`, `flow`).
**Target**: sandbox → `PDK/_bench/Navigation`.
**Source**: M25 "Navigation" and "Composition" walkthroughs (`examples/m24-navigation.yaml`, `examples/m24-composition.yaml`).
**Estimated manual time (Designer)**: _fill in_ min
