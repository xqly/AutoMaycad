#!/usr/bin/env python3
"""Generate a simple aluminum-profile cabinet MAYCAD scene and 2D views.

Input: JSON spec with finished dimensions in millimeters.
Output: plain XML .scene, HTML three-view drawing, and summary JSON.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape


Point = Tuple[float, float, float]


def cm(mm: float) -> float:
    return round(float(mm) / 10.0, 4)


def fmt(value: float) -> str:
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return text if text else "0"


def safe_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip())
    return name.strip("._") or "maycad_cabinet"


def float_list(value: object, field_name: str) -> List[float]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list of positive numbers")
    numbers = [float(item) for item in value]
    if not numbers or any(item <= 0 for item in numbers):
        raise ValueError(f"{field_name} must contain positive numbers")
    return numbers


def bay_widths_for_spec(spec: Dict, length: float, columns: int) -> Tuple[List[float], List[float], bool]:
    if "bay_widths_mm" not in spec:
        width = length / columns
        widths = [width for _ in range(columns)]
        divisions = [i * width for i in range(columns + 1)]
        return widths, divisions, False

    widths = float_list(spec["bay_widths_mm"], "bay_widths_mm")
    if len(widths) != columns:
        raise ValueError("bay_widths_mm length must match columns")
    total = sum(widths)
    if abs(total - length) > 1.0:
        raise ValueError(f"bay_widths_mm must sum to finished length {length:g} mm; got {total:g} mm")

    divisions = [0.0]
    for width in widths:
        divisions.append(divisions[-1] + width)
    divisions[-1] = length
    return widths, divisions, True


def bay_specs_for_spec(spec: Dict, columns: int) -> List[Dict]:
    raw_bays = spec.get("bays", [])
    if raw_bays is None:
        raw_bays = []
    if not isinstance(raw_bays, list):
        raise ValueError("bays must be a list")
    if len(raw_bays) > columns:
        raise ValueError("bays cannot contain more entries than columns")
    bays = [dict(item) if isinstance(item, dict) else {} for item in raw_bays]
    while len(bays) < columns:
        bays.append({})
    return bays


def int_from_bay(bay: Dict, *names: str, default: int = 0) -> int:
    for name in names:
        if name in bay:
            return max(0, int(bay[name]))
    return default


def vec_xml(point: Sequence[float]) -> str:
    x, y, z = point
    return (
        "<Vector3d>"
        f"<x>{fmt(x)}</x><y>{fmt(y)}</y><z>{fmt(z)}</z>"
        "</Vector3d>"
    )


def scene_point_cm(point: Sequence[float]) -> Tuple[float, float, float]:
    """Map semantic (X length, depth, height) mm to MAYCAD (X length, Y height, Z depth) cm."""
    x, depth, height = point
    return (cm(x), cm(height), cm(depth))


def scene_axis(axis: str) -> str:
    """Map semantic axes to MAYCAD scene axes: depth -> Z, height -> Y."""
    mapping = {"X": "X", "Y": "Z", "Z": "Y"}
    try:
        return mapping[axis]
    except KeyError as exc:
        raise ValueError(f"unsupported axis: {axis}") from exc


def actual_matrix(
    forward: Sequence[float], up: Sequence[float], side: Sequence[float], pos: Sequence[float]
) -> List[List[float]]:
    fx, fy, fz = forward
    ux, uy, uz = up
    sx, sy, sz = side
    px, py, pz = pos
    return [
        [fx, fy, fz, 0.0],
        [ux, uy, uz, 0.0],
        [sx, sy, sz, 0.0],
        [px, py, pz, 1.0],
    ]


def serialized_rotation(matrix: List[List[float]]) -> str:
    transposed = [[matrix[j][i] for j in range(4)] for i in range(4)]
    return ",".join(fmt(transposed[i][j]) for i in range(4) for j in range(4))


def basis_for_axis(axis: str, pos: Sequence[float]) -> List[List[float]]:
    if axis == "X":
        return actual_matrix((0, 1, 0), (1, 0, 0), (0, 0, -1), pos)
    if axis == "Y":
        return actual_matrix((1, 0, 0), (0, 1, 0), (0, 0, 1), pos)
    if axis == "Z":
        return actual_matrix((1, 0, 0), (0, 0, 1), (0, -1, 0), pos)
    raise ValueError(f"unsupported axis: {axis}")


class SceneBuilder:
    def __init__(self, spec: Dict):
        self.spec = spec
        self.profile_uid = spec.get("profile_uid", "PROF40-4040L")
        self.panel_uid = spec.get("panel_uid", "PANL_CHIP_MDF-18MM")
        self.profile_size = float(spec.get("profile_size_mm", 40))
        self.panel_thickness = float(spec.get("panel_thickness_mm", 18))
        self.drawer_front_thickness = float(spec.get("drawer_front_thickness_mm", self.panel_thickness))
        self.objects: List[str] = []
        self.profile_count = 0
        self.panel_count = 0
        self.next_id = 1
        self.assumptions: List[str] = []

    def common_entity_xml(
        self,
        kind: str,
        object_id: int,
        name: str,
        profile_uid: str,
        rotation: str,
        comment: str = "",
        color: str | None = None,
    ) -> str:
        xml = ""
        if color:
            xml += f"<cust_color>{escape(color)}</cust_color>"
        xml += f"<type>{kind}</type><id>{object_id}</id><name>{escape(name)}</name>"
        if comment:
            xml += f"<comment><![CDATA[{comment}]]></comment>"
        xml += f"<rotation>{rotation}</rotation>"
        xml += f"<position>{vec_xml((0, 0, 0))}</position>"
        xml += f"<profile>{escape(profile_uid)}</profile>"
        xml += (
            "<bom_exclude>0</bom_exclude><black_finish>0</black_finish>"
            "<is_anchored>0</is_anchored><is_conveyor_part>0</is_conveyor_part>"
        )
        return xml

    def add_profile(self, name: str, axis: str, start_mm: Point, length_mm: float, comment: str = "") -> int:
        if length_mm <= 0:
            raise ValueError(f"profile {name!r} has non-positive length {length_mm}")
        object_id = self.next_id
        self.next_id += 1
        pos = scene_point_cm(start_mm)
        rotation = serialized_rotation(basis_for_axis(scene_axis(axis), pos))
        size_cm = cm(self.profile_size)
        xml = f"<height>{fmt(cm(length_mm))}</height><width>{fmt(size_cm)}</width><length>{fmt(size_cm)}</length>"
        xml += (
            "<topcoverid>0</topcoverid><botcoverid>0</botcoverid>"
            "<radius_cover_bottom>0</radius_cover_bottom><radius_cover_top>0</radius_cover_top>"
            "<radius_cover_thickness>0</radius_cover_thickness>"
        )
        xml += self.common_entity_xml("Profile", object_id, name, self.profile_uid, rotation, comment)
        self.objects.append(xml)
        self.profile_count += 1
        return object_id

    def add_panel(self, name: str, points_mm: Iterable[Point], comment: str = "", color: str = "#9A6A38") -> int:
        object_id = self.next_id
        self.next_id += 1
        points = [scene_point_cm(point) for point in points_mm]
        rotation = serialized_rotation(actual_matrix((1, 0, 0), (0, 1, 0), (0, 0, 1), (0, 0, 0)))
        contour = "".join(vec_xml(point) for point in points)
        xml = f"<points_count>{len(points)}</points_count><contour>{contour}</contour>"
        xml += f"<expanded_points_count>{len(points)}</expanded_points_count><expanded_contour>{contour}</expanded_contour>"
        xml += (
            "<pseudo_slots_count>0</pseudo_slots_count><pseudo_slot_list></pseudo_slot_list>"
            "<pseudo_holes_count>0</pseudo_holes_count><pseudo_hole_list></pseudo_hole_list>"
            "<custom_mount>0</custom_mount><framed_panel>0</framed_panel>"
            "<fastener_screw_type>FHSCS</fastener_screw_type><fastener_nut_type>ECO_T_NUT</fastener_nut_type>"
            "<fastener_step>50</fastener_step><fastener_end_offset>6</fastener_end_offset>"
        )
        xml += self.common_entity_xml("Panel", object_id, name, self.panel_uid, rotation, comment, color)
        self.objects.append(xml)
        self.panel_count += 1
        return object_id

    def build(self) -> Dict:
        dims = self.spec["finished_mm"]
        length = float(dims["length"])
        depth = float(dims["depth"])
        height = float(dims["height"])
        default_columns = len(self.spec["bay_widths_mm"]) if isinstance(self.spec.get("bay_widths_mm"), list) else 1
        columns = max(1, int(self.spec.get("columns", default_columns)))
        layers = max(1, int(self.spec.get("layers", 1)))
        legacy_drawers = max(0, int(self.spec.get("drawers", 0)))
        bay_widths, front_divisions, custom_bay_widths = bay_widths_for_spec(self.spec, length, columns)
        bay_specs = bay_specs_for_spec(self.spec, columns)
        uses_bay_features = "bays" in self.spec
        bay_feature_drawers = sum(int_from_bay(bay, "drawers", "drawer_count") for bay in bay_specs)
        drawers = bay_feature_drawers if uses_bay_features else legacy_drawers
        slide_length = float(self.spec.get("drawer_slide_length_mm", max(80, depth - 150)))
        include_panels = bool(self.spec.get("include_panels", True))

        p = self.profile_size
        half = p / 2.0
        t = self.panel_thickness
        front_t = self.drawer_front_thickness

        frame_length = float(self.spec.get("frame_length_mm", length - 2 * t))
        frame_depth = float(self.spec.get("frame_depth_mm", depth - front_t))
        frame_height = float(self.spec.get("frame_height_mm", height - t))
        if frame_length <= p or frame_depth <= p or frame_height <= p:
            raise ValueError("finished dimensions are too small for the selected profile and panel thickness")

        self.assumptions.append("finished_mm is treated as the finished outer size")
        self.assumptions.append("frame size subtracts side panels, drawer-front depth, and top panel height")
        if custom_bay_widths:
            self.assumptions.append("bay_widths_mm is treated as finished front bay widths from left to right")

        x_min = t + half
        x_max = t + frame_length - half
        if custom_bay_widths:
            x_lines = [x_min] + front_divisions[1:-1] + [x_max]
        else:
            x_lines = [x_min + i * (x_max - x_min) / columns for i in range(columns + 1)]
        if any(x_lines[i + 1] - x_lines[i] <= p for i in range(columns)):
            raise ValueError("bay widths leave insufficient span for the selected profile size")
        y_front = front_t + half
        y_back = depth - half
        z_lines = [half + i * (frame_height - p) / layers for i in range(layers + 1)]

        for x in x_lines:
            for y in (y_front, y_back):
                self.add_profile(f"vertical post X{x:.1f} Y{y:.1f}", "Z", (x, y, 0), frame_height)

        for z in z_lines:
            for y in (y_front, y_back):
                for i in range(columns):
                    start_x = x_lines[i] + half
                    end_x = x_lines[i + 1] - half
                    self.add_profile(f"X beam bay {i + 1} Y{y:.1f} Z{z:.1f}", "X", (start_x, y, z), end_x - start_x)
            for x in x_lines:
                self.add_profile(f"depth beam X{x:.1f} Z{z:.1f}", "Y", (x, y_front + half, z), y_back - y_front - p)

        if legacy_drawers and not uses_bay_features:
            drawer_count = min(legacy_drawers, columns)
            slide_z = float(self.spec.get("drawer_slide_z_mm", max(half + p, frame_height * 0.42)))
            slide_start_y = min(y_front + p * 0.8, depth - p - 80)
            slide_len = min(slide_length, max(80, y_back - slide_start_y - half))
            for bay in range(drawer_count):
                bay_left = x_lines[bay]
                bay_right = x_lines[bay + 1]
                support_left = bay_left + p * 0.8
                support_right = bay_right - p * 0.8
                self.add_profile(
                    f"drawer {bay + 1} left slide support",
                    "Y",
                    (support_left, slide_start_y, slide_z),
                    slide_len,
                    "drawer slide support rail",
                )
                self.add_profile(
                    f"drawer {bay + 1} right slide support",
                    "Y",
                    (support_right, slide_start_y, slide_z),
                    slide_len,
                    "drawer slide support rail",
                )

        bay_features = []
        if uses_bay_features:
            for bay_index, bay in enumerate(bay_specs):
                bay_name = str(bay.get("name") or bay.get("label") or f"bay {bay_index + 1}")
                shelf_count = int_from_bay(bay, "shelves", "shelf_count")
                drawer_count = int_from_bay(bay, "drawers", "drawer_count")
                hanging_rod = bool(bay.get("hanging_rod") or bay.get("rod"))
                bay_features.append(
                    {
                        "index": bay_index + 1,
                        "name": bay_name,
                        "shelves": shelf_count,
                        "drawers": drawer_count,
                        "hanging_rod": hanging_rod,
                    }
                )

                x1 = x_lines[bay_index] + p * 0.75
                x2 = x_lines[bay_index + 1] - p * 0.75
                if x2 <= x1:
                    raise ValueError(f"bay {bay_index + 1} is too narrow for internal features")
                panel_y1 = max(front_t, t)
                panel_y2 = depth - t

                if shelf_count:
                    shelf_z_values = bay.get("shelf_z_mm")
                    if shelf_z_values is not None:
                        z_values = float_list(shelf_z_values, "shelf_z_mm")
                    else:
                        z_values = [frame_height * (shelf + 1) / (shelf_count + 1) for shelf in range(shelf_count)]
                    for shelf_number, z in enumerate(z_values, start=1):
                        z = min(max(float(z), p * 1.5), frame_height - p * 1.5)
                        self.add_panel(
                            f"{bay_name} shelf {shelf_number}",
                            [(x1, panel_y1, z), (x2, panel_y1, z), (x2, panel_y2, z), (x1, panel_y2, z)],
                            f"horizontal shelf in bay {bay_index + 1}",
                            "#B9824F",
                        )

                if hanging_rod:
                    rod_z = float(bay.get("rod_z_mm", max(frame_height - 420, frame_height * 0.78)))
                    rod_y = float(bay.get("rod_y_mm", depth * 0.48))
                    self.add_profile(
                        f"{bay_name} hanging rod",
                        "X",
                        (x1, rod_y, min(rod_z, frame_height - p * 1.5)),
                        x2 - x1,
                        "hanging rail placeholder profile",
                    )

                if drawer_count:
                    gap = float(bay.get("drawer_gap_mm", self.spec.get("drawer_gap_mm", 15)))
                    stack_height = float(bay.get("drawer_stack_height_mm", min(900, frame_height * 0.34)))
                    base_z = float(bay.get("drawer_base_z_mm", gap))
                    usable = max(80, stack_height - gap * (drawer_count + 1))
                    front_height = float(bay.get("drawer_front_height_mm", usable / drawer_count))
                    slide_start_y = min(y_front + p * 0.8, depth - p - 80)
                    slide_len = min(slide_length, max(80, y_back - slide_start_y - half))
                    for drawer_number in range(drawer_count):
                        z1 = base_z + gap + drawer_number * (front_height + gap)
                        z2 = min(z1 + front_height, frame_height - gap)
                        y = front_t / 2.0
                        self.add_panel(
                            f"{bay_name} drawer front {drawer_number + 1}",
                            [(x1 + gap, y, z1), (x2 - gap, y, z1), (x2 - gap, y, z2), (x1 + gap, y, z2)],
                            f"decorative drawer front in bay {bay_index + 1}",
                        )
                        slide_z = min(z1 + front_height * 0.5, frame_height - p * 1.5)
                        self.add_profile(
                            f"{bay_name} drawer {drawer_number + 1} left slide support",
                            "Y",
                            (x1 + p * 0.5, slide_start_y, slide_z),
                            slide_len,
                            "drawer slide support rail",
                        )
                        self.add_profile(
                            f"{bay_name} drawer {drawer_number + 1} right slide support",
                            "Y",
                            (x2 - p * 0.5, slide_start_y, slide_z),
                            slide_len,
                            "drawer slide support rail",
                        )

        if include_panels:
            top_z = height - t / 2.0
            self.add_panel(
                "top board",
                [(0, 0, top_z), (length, 0, top_z), (length, depth, top_z), (0, depth, top_z)],
                "decorative top panel",
            )
            self.add_panel(
                "left side panel",
                [(t / 2, 0, 0), (t / 2, 0, frame_height), (t / 2, depth, frame_height), (t / 2, depth, 0)],
                "decorative side panel",
            )
            self.add_panel(
                "right side panel",
                [
                    (length - t / 2, 0, 0),
                    (length - t / 2, depth, 0),
                    (length - t / 2, depth, frame_height),
                    (length - t / 2, 0, frame_height),
                ],
                "decorative side panel",
            )
            if legacy_drawers and not uses_bay_features:
                gap = float(self.spec.get("drawer_gap_mm", 15))
                front_height = float(self.spec.get("drawer_front_height_mm", max(120, frame_height * 0.72)))
                z1 = max(gap, (frame_height - front_height) / 2)
                z2 = min(frame_height - gap, z1 + front_height)
                for bay in range(min(legacy_drawers, columns)):
                    x1 = bay * length / columns + gap
                    x2 = (bay + 1) * length / columns - gap
                    y = front_t / 2.0
                    self.add_panel(
                        f"drawer front {bay + 1}",
                        [(x1, y, z1), (x2, y, z1), (x2, y, z2), (x1, y, z2)],
                        "decorative drawer front",
                    )

        return {
            "length": length,
            "depth": depth,
            "height": height,
            "frame_length": frame_length,
            "frame_depth": frame_depth,
            "frame_height": frame_height,
            "columns": columns,
            "bay_widths": bay_widths,
            "front_divisions": front_divisions,
            "bay_features": bay_features,
            "layers": layers,
            "drawers": drawers,
        }

    def scene_xml(self, title: str, description: str) -> str:
        scene = '<?xml version="1.0" encoding="UTF-8" ?>\n<scene>'
        scene += (
            "<version>14</version><software_branch_uid>win_maytec_maycad_64</software_branch_uid>"
            "<software_version>12.10</software_version><isparamsample>0</isparamsample>"
            "<software_computer_id>codex-maycad-skill</software_computer_id>"
            "<software_session_id>codex-maycad-skill</software_session_id>"
            "<settings_market>0</settings_market><settings_vendor_idx>1</settings_vendor_idx>"
            "<settings_currency>0</settings_currency><settings_bom_lang>0</settings_bom_lang>"
            "<vendors>MAYTEC</vendors><metric>1</metric><enable_parallels>0</enable_parallels><objects>\n"
        )
        for obj in self.objects:
            scene += f"<object>{obj}</object>\n"
        scene += "</objects>\n<variable_manager></variable_manager>"
        scene += (
            "<AuthorName><![CDATA[Codex]]></AuthorName><AuthorLastName><![CDATA[]]></AuthorLastName>"
            "<AuthorEmail><![CDATA[]]></AuthorEmail><AuthorCompany><![CDATA[]]></AuthorCompany>"
            "<AuthorUUID><![CDATA[codex-maycad-skill]]></AuthorUUID>"
            f"<design_title><![CDATA[{title}]]></design_title>"
            f"<design_description><![CDATA[{description}]]></design_description>"
            "<revision_level><![CDATA[01]]></revision_level><approved_by><![CDATA[]]></approved_by></scene>"
        )
        ET.fromstring(scene)
        return scene


def svg_rect(x: float, y: float, w: float, h: float, cls: str = "line") -> str:
    return f'<rect class="{cls}" x="{fmt(x)}" y="{fmt(y)}" width="{fmt(w)}" height="{fmt(h)}" />'


def svg_line(x1: float, y1: float, x2: float, y2: float, cls: str = "line") -> str:
    return f'<line class="{cls}" x1="{fmt(x1)}" y1="{fmt(y1)}" x2="{fmt(x2)}" y2="{fmt(y2)}" />'


def svg_text(x: float, y: float, text: object, cls: str = "label", anchor: str = "middle") -> str:
    return f'<text class="{cls}" x="{fmt(x)}" y="{fmt(y)}" text-anchor="{anchor}">{escape(str(text))}</text>'


def generate_three_views(spec: Dict, built: Dict, title: str) -> str:
    length, depth, height = built["length"], built["depth"], built["height"]
    columns, drawers = built["columns"], built["drawers"]
    bay_widths = built.get("bay_widths") or [length / columns for _ in range(columns)]
    front_divisions = built.get("front_divisions") or [i * length / columns for i in range(columns + 1)]
    bay_features = built.get("bay_features") or []
    scale = min(720 / max(length, 1), 260 / max(height, depth, 1))
    front_w, front_h = length * scale, height * scale
    top_h = depth * scale
    side_w, side_h = depth * scale, height * scale
    col_lines = [front_w * division / length for division in front_divisions]

    front = [svg_rect(0, 0, front_w, front_h)]
    for x in col_lines[1:-1]:
        front.append(svg_line(x, 0, x, front_h, "division"))
    feature_by_index = {int(item.get("index", 0)): item for item in bay_features}
    for i in range(columns):
        x1, x2 = col_lines[i], col_lines[i + 1]
        bay_w = x2 - x1
        feature = feature_by_index.get(i + 1, {})
        front.append(svg_text(x1 + bay_w / 2, front_h + 22, f"{fmt(bay_widths[i])} mm", "dimension"))
        if feature.get("name"):
            front.append(svg_text(x1 + bay_w / 2, -10, feature["name"], "bay-label"))
        shelves = int(feature.get("shelves", 0) or 0)
        for shelf in range(shelves):
            y = front_h - front_h * (shelf + 1) / (shelves + 1)
            front.append(svg_rect(x1 + 8, y - 3, max(4, bay_w - 16), 6, "shelf"))
        if feature.get("hanging_rod"):
            y = front_h * 0.18
            front.append(svg_line(x1 + 14, y, x2 - 14, y, "rod"))
            front.append(svg_line(x1 + 14, y, x1 + 14, y + 22, "rod-drop"))
            front.append(svg_line(x2 - 14, y, x2 - 14, y + 22, "rod-drop"))
        drawer_count = int(feature.get("drawers", 0) or 0)
        if drawer_count:
            gap = 8
            stack_h = min(front_h * 0.34, 84)
            drawer_h = max(12, (stack_h - gap * (drawer_count + 1)) / drawer_count)
            for drawer in range(drawer_count):
                y = front_h - stack_h + gap + drawer * (drawer_h + gap)
                front.append(svg_rect(x1 + gap, y, max(4, bay_w - gap * 2), drawer_h, "panel"))

    top = [svg_rect(0, 0, front_w, top_h)]
    for x in col_lines[1:-1]:
        top.append(svg_line(x, 0, x, top_h, "division"))
    for i in range(columns):
        x1, x2 = col_lines[i], col_lines[i + 1]
        top.append(svg_text((x1 + x2) / 2, top_h + 18, fmt(bay_widths[i]), "dimension"))

    side = [svg_rect(0, 0, side_w, side_h)]
    side.append(svg_line(0, side_h * 0.2, side_w, side_h * 0.2, "division"))
    side.append(svg_line(0, side_h * 0.82, side_w, side_h * 0.82, "division"))

    assumptions = "".join(f"<li>{escape(item)}</li>" for item in spec.get("assumptions", []))
    bay_width_note = " / ".join(f"{fmt(width)}" for width in bay_widths)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{escape(title)} three views</title>
<style>
body {{ font-family: Arial, sans-serif; margin: 24px; color: #1f2937; background: #f7f7f2; }}
.sheet {{ max-width: 980px; margin: auto; background: white; border: 1px solid #d6d3c8; padding: 20px; }}
.views {{ display: grid; grid-template-columns: 1fr; gap: 22px; }}
.view h2 {{ font-size: 16px; margin: 0 0 8px; }}
svg {{ width: 100%; height: auto; background: #fbfaf6; border: 1px solid #ddd8ca; }}
.line {{ fill: none; stroke: #1f2937; stroke-width: 2; }}
.division {{ stroke: #6b7280; stroke-width: 1.4; stroke-dasharray: 7 5; }}
.panel {{ fill: rgba(154,106,56,.18); stroke: #9a6a38; stroke-width: 1.6; }}
.shelf {{ fill: rgba(185,130,79,.28); stroke: #9a6a38; stroke-width: 1.2; }}
.rod {{ stroke: #475569; stroke-width: 3; }}
.rod-drop {{ stroke: #475569; stroke-width: 1.4; }}
.label, .dimension, .bay-label {{ fill: #1f2937; font-size: 13px; }}
.dimension {{ fill: #4b5563; }}
.bay-label {{ font-size: 12px; }}
.note {{ color: #4b5563; font-size: 14px; line-height: 1.5; }}
</style>
</head>
<body>
<main class="sheet">
<h1>{escape(title)}</h1>
<p class="note">Finished size: {fmt(length)} x {fmt(depth)} x {fmt(height)} mm. Bay widths: {escape(bay_width_note)} mm. Columns: {columns}. Drawers: {drawers}.</p>
<section class="views">
<div class="view"><h2>Front view</h2><svg viewBox="-20 -34 {fmt(front_w + 60)} {fmt(front_h + 74)}">{''.join(front)}{svg_text(0, -18, f"L {fmt(length)} mm", "dimension", "start")}{svg_text(front_w + 8, front_h / 2, f"H {fmt(height)} mm", "dimension", "start")}</svg></div>
<div class="view"><h2>Top view</h2><svg viewBox="-20 -28 {fmt(front_w + 60)} {fmt(top_h + 70)}">{''.join(top)}{svg_text(0, -8, f"L {fmt(length)} mm", "dimension", "start")}{svg_text(front_w + 8, top_h / 2, f"D {fmt(depth)} mm", "dimension", "start")}</svg></div>
<div class="view"><h2>Side view</h2><svg viewBox="-20 -28 {fmt(side_w + 80)} {fmt(side_h + 56)}">{''.join(side)}{svg_text(0, -8, f"D {fmt(depth)} mm", "dimension", "start")}{svg_text(side_w + 8, side_h / 2, f"H {fmt(height)} mm", "dimension", "start")}</svg></div>
</section>
<h2>Assumptions</h2>
<ul class="note">{assumptions}</ul>
</main>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", required=True, help="Path to JSON spec")
    parser.add_argument("--output-dir", required=True, help="Output directory")
    args = parser.parse_args()

    spec_path = Path(args.spec)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    spec = json.loads(spec_path.read_text(encoding="utf-8-sig"))
    project_name = safe_name(spec.get("project_name", "maycad_cabinet"))
    title = spec.get("title", project_name.replace("_", " ").title())
    description = spec.get(
        "description",
        "Generated aluminum-profile cabinet with 2D three-view drawing and MAYCAD scene.",
    )

    builder = SceneBuilder(spec)
    input_assumptions = [str(item) for item in spec.get("assumptions", [])]
    built = builder.build()
    spec["assumptions"] = input_assumptions + builder.assumptions

    scene_path = output_dir / f"{project_name}.scene"
    html_path = output_dir / f"{project_name}_three_views.html"
    summary_path = output_dir / f"{project_name}_summary.json"

    scene_path.write_text(builder.scene_xml(title, description), encoding="utf-8")
    html_path.write_text(generate_three_views(spec, built, title), encoding="utf-8")
    summary = {
        "project_name": project_name,
        "scene": str(scene_path),
        "three_views": str(html_path),
        "objects": len(builder.objects),
        "profiles": builder.profile_count,
        "panels": builder.panel_count,
        "built": built,
        "assumptions": spec["assumptions"],
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
