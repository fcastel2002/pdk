# 03 · Equipment panel in coordinates

**Request**
> Build me a fixed 640×480 panel for equipment P-101:
> - at the top, the title "Equipment P-101";
> - on the left, an image of the equipment;
> - on the right, a box with a "Rated load" bar scaled from 20 to 80 % (minimum 20, maximum 80) showing 65, and an "Available" status.
>
> Use a coordinate container.

**Acceptance criteria**
- `pdk_validate` with no errors or warnings.
- Visible texts: "Equipment P-101", "Rated load".
- Minimum components: 1 coordinate container, 1 image, 1 progress bar.
- The bar has `min` 20, `max` 80 and `value` 65.
- Every child has a `position` with `x`, `y`, `width` and `height` (no flex keys).
- The image uses a built-in path (`/system/images/Builtin/...`) and loads without errors (verified with `inspect`).
- No overlaps or overflows at 1366×768 (verified with `inspect`).

**Data**: static.
**Target**: sandbox → `PDK/_bench/EquipmentPanel`.
**Source**: M25 "Equipment panel" walkthrough (`examples/m20-equipment-coordinate.yaml`).
**Estimated manual time (Designer)**: _fill in_ min
