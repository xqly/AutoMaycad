# MAYCAD Scene Format Reference

Use this when manually editing or debugging generated `.scene` XML.

## File Shape

MAYCAD 12.10 can load plain XML `.scene` files. Own saved files may be encrypted, but load detection is simply whether the first five bytes are `<?xml`.

Required outer structure:

```xml
<?xml version="1.0" encoding="UTF-8" ?>
<scene>
  <version>14</version>
  <software_branch_uid>win_maytec_maycad_64</software_branch_uid>
  <software_version>12.10</software_version>
  <vendors>MAYTEC</vendors>
  <metric>1</metric>
  <enable_parallels>0</enable_parallels>
  <objects>
    <object>...</object>
  </objects>
  <variable_manager></variable_manager>
</scene>
```

## Units

MAYCAD model units in scene XML are centimeters. Convert dimensions and coordinates:

```text
scene_value = millimeters / 10
```

Examples: `2400 mm -> 240`, `40 mm -> 4`, `362 mm -> 36.2`.

## Coordinate Convention

Use this skill's semantic coordinate system for furniture modeling:

```text
X = length / front width
Z = width / depth
Y = height
```

When a helper script or manual calculation uses semantic points as `(x, depth, height)`, write them to MAYCAD scene XML as:

```text
scene_x = x / 10
scene_y = height / 10
scene_z = depth / 10
```

Do not use the older `Y=depth, Z=height` convention for new outputs.

## Profiles

Use type `Profile` with profile UID `PROF40-4040L` for 4040 unless the user requests another catalog profile.

The profile length is stored in `<height>`, not `<length>`. For 4040, write:

```xml
<height>73.47</height>
<width>4</width>
<length>4</length>
...
<type>Profile</type>
<profile>PROF40-4040L</profile>
```

## Transform Matrix

MAYCAD serializes a transposed 4x4 matrix in `<rotation>`. When building a profile, create an actual basis matrix where the second row (`up`) is the profile extrusion direction, then serialize its transpose.

Suggested basis by extrusion direction:

```text
X axis: forward=(0,1,0), up=(1,0,0), side=(0,0,-1)
Y axis: forward=(1,0,0), up=(0,1,0), side=(0,0,1)
Z axis: forward=(1,0,0), up=(0,0,1), side=(0,-1,0)
```

Store translation in the matrix, and keep `<position>` as zero:

```xml
<position><Vector3d><x>0</x><y>0</y><z>0</z></Vector3d></position>
```

## Panels

Use type `Panel` with a rectangular contour. `PANL_CHIP_MDF-18MM` gives 18 mm MDF geometry in the current MAYCAD catalog, but can emit non-fatal BOM warnings. Contour points are in scene units.

Panel fields expected by the loader:

```xml
<points_count>4</points_count>
<contour>...</contour>
<expanded_points_count>4</expanded_points_count>
<expanded_contour>...</expanded_contour>
<pseudo_slots_count>0</pseudo_slots_count>
<pseudo_slot_list></pseudo_slot_list>
<pseudo_holes_count>0</pseudo_holes_count>
<pseudo_hole_list></pseudo_hole_list>
<custom_mount>0</custom_mount>
<framed_panel>0</framed_panel>
<type>Panel</type>
<profile>PANL_CHIP_MDF-18MM</profile>
```

## Verification

After opening a generated file, inspect:

- MAYCAD window title includes the scene file path.
- `print_debug.log` contains `READ FILE VERSION = 14`, `Finish loading entities`, and `SCENE LOADED IN`.
- Treat `SCENE HAS UNRECOGNIZED PARTS` for MDF panels as a geometry-loaded/BOM-warning state, not a scene-load failure.
