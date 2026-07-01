---
name: maycad
description: Use when creating MAYCAD aluminum-profile drawings or models from numeric dimensions, text descriptions, rough sketches, reference images, or style images; includes cabinets, racks, mobile frames with casters, custom sloped/diagonal frames, 2D three-view drawings, batch scene generation, and MAYCAD .scene verification.
---

# MAYCAD

## Overview

Turn dimensions, text, or reference images into a checked 2D three-view drawing first, then generate a MAYCAD `.scene` model for aluminum-profile furniture, cabinets, racks, frames, and similar assemblies.

This is a file-generation workflow, not a live GUI drawing workflow. Generate a valid MAYCAD plain-XML `.scene` file with scripts or explicit XML construction, then launch MAYCAD only to open and verify the generated engineering file. Use Computer Use only for GUI-only operations such as manual menu export, screenshots, or visual inspection that cannot be verified from files/window titles.

## Workflow

1. Normalize the input.
   - Extract dimensions in mm, object type, finished size, profile series, panel thickness, drawers/doors/layers/columns, unequal bay widths, and visible style cues.
   - Extract mobile-base details such as casters, feet, leveling pads, toe-kicks, and whether the stated height includes them.
   - For sloped tops, diagonal braces, irregular corners, or sketch-like perspective drawings, convert the visible intent into explicit 3D points and label assumptions.
   - When the main source is a reference image or sketch, default to an aluminum-profile frame-only model. Do not add boards, shelf panels, door faces, glass, acrylic, MDF, decorative panels, or wood panels unless the user explicitly asks for those materials.
   - If the user provides only text or an image, make practical assumptions and label them. Ask only when a missing value would materially change the structure.
   - Default MAYCAD model coordinate system: `X = length`, `Z = width/depth`, `Y = height`. Long horizontal members run along X, depth members run along Z, and vertical uprights run along Y.

2. Create a compact spec before modeling.
   - Store a JSON spec in the working folder when using the bundled script.
   - Treat user dimensions as finished outer dimensions unless they clearly mean bare frame dimensions.
   - For non-equal front sections, set `bay_widths_mm` left-to-right; values must sum to `finished_mm.length`.
   - For wardrobe/cabinet sections, set `bays` left-to-right with `name`, `shelves`, `hanging_rod`, and/or `drawers`.
   - If casters are requested and the user does not specify caster height, assume `80 mm` and state whether total height includes the casters. Default: user height is finished overall height including casters.
   - For reference-image/sketch jobs, set `include_panels: false` unless the user explicitly says to add boards/panels/shelves/wood/glass/acrylic/MDF. Treat visible panels in the image as frame openings or notes, not generated MAYCAD panel objects.
   - Defaults: `4040` aluminum profile, `18 mm` wood/MDF decorative board, `MAYTEC` vendor, `PROF40-4040L`, `PANL_CHIP_MDF-18MM`.

3. Generate the 2D three-view drawing first.
   - Produce front, top, and side views with overall dimensions, bay divisions, openings, optional panels, drawers/doors, and assumptions.
   - For custom sketches, show key sloped/diagonal members, door/opening zones, caster locations, and any inferred compartment split. If boards are visible but not explicitly requested, mark them as "not modeled / user must request panels".
   - Use SVG/HTML when possible so the user can inspect proportions quickly.
   - Do not proceed blindly if the three-view drawing contradicts the user’s stated dimensions.

4. Generate the MAYCAD model.
   - For rectangular cabinet/frame work, prefer `scripts/generate_maycad_cabinet.py`.
   - For custom geometry, adapt the generated `.scene` XML using `references/maycad-scene-format.md`.
   - For reference-image/sketch modeling, generate `Profile` objects only by default. Add `Panel` objects only when the user explicitly requests boards, shelves, door panels, side panels, glass/acrylic, MDF, or wood decorative panels.
   - Build every panel as outside-mounted, inset within clear opening dimensions, or sitting above/supporting on profiles; never let a panel share physical volume with a profile.
   - Keep object names descriptive: profile direction, bay, panel role, drawer number, or support function.

5. Open and verify in MAYCAD.
   - Launch `framedesigner.exe` with the generated `.scene` file.
   - Verify a MAYCAD window title contains the generated scene path.
   - Check `print_debug.log` for `SCENE LOADED IN` and object-loading messages.
   - Before marking the task verified, manually check the generated scene geometry for obvious `Panel`/`Profile` collisions using object coordinates, panel thickness, and profile size.
   - If the workspace path contains non-ASCII characters or MAYCAD starts without showing the scene path, make an ASCII verification copy under `C:\Users\WINDOWS\Documents\maycad_output\`, launch that copy, and verify the window title against the copied path.
   - Treat the ASCII verification copy as a load-test artifact only; keep the workspace `.scene`, spec, summary, and three-view files as the primary deliverables.
   - Report non-fatal panel/BOM warnings separately from load failures.

6. Deliver concise outputs.
   - Provide the primary workspace `.scene` path, the 2D view path, object counts, assumptions, and remaining production-level gaps such as connectors, machining holes, exact board SKU, or drawer-slide SKU.
   - If the source was a reference image and the user did not explicitly request boards, state that the model is frame-only and that visible boards/panels were not generated.
   - If verification used an ASCII verification copy, also report the copied path and the MAYCAD window title that confirmed loading.

## Script Use

Create a JSON spec and run:

```powershell
python C:\Users\WINDOWS\.codex\skills\maycad\scripts\generate_maycad_cabinet.py --spec C:\path\to\spec.json --output-dir C:\path\to\out
```

Minimal spec:

```json
{
  "project_name": "tv_cabinet",
  "finished_mm": { "length": 2400, "depth": 400, "height": 380 },
  "coordinate_system": { "x": "length", "z": "depth/width", "y": "height" },
  "columns": 3,
  "layers": 1,
  "drawers": 3,
  "profile_size_mm": 40,
  "panel_thickness_mm": 18,
  "drawer_slide_length_mm": 250
}
```

Reference-image frame-only spec:

```json
{
  "project_name": "frame_from_reference_image",
  "source": "reference image",
  "finished_mm": { "length": 500, "depth": 500, "height": 1200 },
  "coordinate_system": { "x": "length", "z": "depth/width", "y": "height" },
  "include_panels": false,
  "profile_size_mm": 40,
  "assumptions": [
    "Reference images default to aluminum-profile frame-only output.",
    "Boards, shelves, door faces, glass/acrylic, MDF, and wood panels require an explicit user instruction."
  ]
}
```

Unequal-width wardrobe spec:

```json
{
  "project_name": "wardrobe_5110x600x3100",
  "finished_mm": { "length": 5110, "depth": 600, "height": 3100 },
  "coordinate_system": { "x": "length", "z": "depth/width", "y": "height" },
  "columns": 3,
  "bay_widths_mm": [2130, 1220, 1760],
  "bays": [
    { "name": "left shelves", "shelves": 3 },
    { "name": "middle open" },
    { "name": "right hanging drawers", "hanging_rod": true, "drawers": 2 }
  ],
  "profile_size_mm": 40,
  "panel_thickness_mm": 18
}
```

The script writes:

- `<project>.scene`: MAYCAD plain XML project file
- `<project>_three_views.html`: visual front/top/side drawing
- `<project>_summary.json`: object counts and major assumptions

## Anti-Collision Rules

- The final `.scene` must not contain physical overlap between `Panel` and `Profile` objects.
- Treat `PROF40-4040L` 4040 profiles as real `40 mm x 40 mm` solids. Treat `PANL_CHIP_MDF-18MM` panels as real `18 mm` thick solids, not zero-thickness faces.
- Keep a default `1-2 mm` assembly clearance between panels and profiles unless the user gives tighter production details.
- Drawer fronts, door fronts, and decorative front panels must sit outside the front frame and avoid front horizontal rails. If the front frame occupies depth `0-40 mm`, an `18 mm` front panel should be centered outside that range, such as at depth `-9 mm` or beyond the outside face, not at depth `+9 mm`.
- Shelf boards must use clear opening dimensions in X and depth, and their height must sit above support rails or inside a clear opening. They must not cross a horizontal rail centerline or pass through vertical posts.
- Side, top, and back panels must either be outside-mounted on the frame exterior or inset to the clear internal opening. Do not create panels directly from finished outer dimensions if that would pass through the 4040 frame volume.
- If a safe panel placement is uncertain, prefer omitting the panel or making a frame-only model, and explain the omission in the summary.
- Set `verified: true` only after the XML parses, object counts are reasonable, and the model has no obvious `Panel`/`Profile` geometry collision. Completion marker `verification_scope` should include `manual geometry collision check for Panel/Profile objects`.

## MAYCAD Notes

- MAYCAD internal scene units are centimeters. Convert all mm values by dividing by `10`.
- This skill uses semantic coordinates `X=length`, `Z=width/depth`, `Y=height`. If a script uses internal variables named `(x, depth, height)`, write them to scene XML as `(x, height, depth)`.
- Plain XML `.scene` files are accepted even though MAYCAD’s own saved scenes may be encrypted.
- `PROF40-4040L` is a reliable default 4040 profile UID in the local MAYTEC catalog.
- `PANL_CHIP_MDF-18MM` creates usable 18 mm MDF panel geometry when panels are explicitly requested, but some MAYCAD catalogs show a non-fatal BOM/item-data warning for this panel. If procurement accuracy matters, replace it with the user’s exact board SKU.
- Generated models are layout/geometry models unless the user explicitly asks for production detail. Add connector sets, machining holes, fasteners, slide SKUs, and cut-processing data only when requested or required.

## Verification Checklist

- The XML parses successfully.
- The scene has nonzero profile objects and any expected panel objects.
- The 2D three-view dimensions match the spec.
- MAYCAD opens the `.scene` and remains responsive.
- For non-ASCII workspace paths, MAYCAD opens the ASCII verification copy and the copied file hash matches the primary workspace `.scene`.
- The final answer distinguishes verified geometry from unresolved manufacturing details.
