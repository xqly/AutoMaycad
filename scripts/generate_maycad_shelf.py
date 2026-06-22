#!/usr/bin/env python3
"""Generate a MAYCAD scene for an aluminum-profile display shelf."""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Iterable, Sequence
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape


Point = tuple[float, float, float]


def cm(mm: float) -> float:
    return round(float(mm) / 10.0, 4)


def fmt(value: float) -> str:
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return text if text else "0"


def safe_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip())
    return name.strip("._") or "maycad_shelf"


def vec_xml(point: Sequence[float]) -> str:
    x, y, z = point
    return (
        "<Vector3d>"
        f"<x>{fmt(x)}</x><y>{fmt(y)}</y><z>{fmt(z)}</z>"
        "</Vector3d>"
    )


def normalize(vector: Sequence[float]) -> Point:
    x, y, z = vector
    length = math.sqrt(x * x + y * y + z * z)
    if length <= 0:
        raise ValueError("cannot normalize a zero-length vector")
    return (x / length, y / length, z / length)


def cross(a: Sequence[float], b: Sequence[float]) -> Point:
    ax, ay, az = a
    bx, by, bz = b
    return (ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx)


def actual_matrix(
    forward: Sequence[float], up: Sequence[float], side: Sequence[float], pos: Sequence[float]
) -> list[list[float]]:
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


def serialized_rotation(matrix: list[list[float]]) -> str:
    transposed = [[matrix[j][i] for j in range(4)] for i in range(4)]
    return ",".join(fmt(transposed[i][j]) for i in range(4) for j in range(4))


def basis_for_axis(axis: str, pos: Sequence[float]) -> list[list[float]]:
    if axis == "X":
        return actual_matrix((0, 1, 0), (1, 0, 0), (0, 0, -1), pos)
    if axis == "Y":
        return actual_matrix((1, 0, 0), (0, 1, 0), (0, 0, 1), pos)
    if axis == "Z":
        return actual_matrix((1, 0, 0), (0, 0, 1), (0, -1, 0), pos)
    raise ValueError(f"unsupported axis: {axis}")


def basis_between(start_mm: Point, end_mm: Point) -> tuple[list[list[float]], float]:
    direction_mm = tuple(end_mm[i] - start_mm[i] for i in range(3))
    length_mm = math.sqrt(sum(item * item for item in direction_mm))
    up = normalize(direction_mm)
    reference = (0.0, 0.0, 1.0)
    if abs(sum(up[i] * reference[i] for i in range(3))) > 0.92:
        reference = (0.0, 1.0, 0.0)
    side = normalize(cross(reference, up))
    forward = normalize(cross(up, side))
    return actual_matrix(forward, up, side, tuple(cm(v) for v in start_mm)), length_mm


class ShelfSceneBuilder:
    def __init__(self, spec: dict):
        self.spec = spec
        self.profile_uid = spec.get("profile_uid", "PROF40-4040L")
        self.panel_uid = spec.get("panel_uid", "PANL_CHIP_MDF-18MM")
        self.profile_size = float(spec.get("profile_size_mm", 40))
        self.panel_thickness = float(spec.get("panel_thickness_mm", 18))
        self.objects: list[str] = []
        self.profile_count = 0
        self.panel_count = 0
        self.next_id = 1
        self.assumptions: list[str] = []

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

    def add_profile(
        self,
        name: str,
        axis: str,
        start_mm: Point,
        length_mm: float,
        comment: str = "",
        color: str = "#AEB5BD",
    ) -> int:
        if length_mm <= 0:
            raise ValueError(f"profile {name!r} has non-positive length {length_mm}")
        object_id = self.next_id
        self.next_id += 1
        pos = tuple(cm(v) for v in start_mm)
        rotation = serialized_rotation(basis_for_axis(axis, pos))
        size_cm = cm(self.profile_size)
        xml = f"<height>{fmt(cm(length_mm))}</height><width>{fmt(size_cm)}</width><length>{fmt(size_cm)}</length>"
        xml += (
            "<topcoverid>0</topcoverid><botcoverid>0</botcoverid>"
            "<radius_cover_bottom>0</radius_cover_bottom><radius_cover_top>0</radius_cover_top>"
            "<radius_cover_thickness>0</radius_cover_thickness>"
        )
        xml += self.common_entity_xml("Profile", object_id, name, self.profile_uid, rotation, comment, color)
        self.objects.append(xml)
        self.profile_count += 1
        return object_id

    def add_profile_between(
        self,
        name: str,
        start_mm: Point,
        end_mm: Point,
        comment: str = "",
        color: str = "#8F98A3",
    ) -> int:
        matrix, length_mm = basis_between(start_mm, end_mm)
        object_id = self.next_id
        self.next_id += 1
        rotation = serialized_rotation(matrix)
        size_cm = cm(self.profile_size)
        xml = f"<height>{fmt(cm(length_mm))}</height><width>{fmt(size_cm)}</width><length>{fmt(size_cm)}</length>"
        xml += (
            "<topcoverid>0</topcoverid><botcoverid>0</botcoverid>"
            "<radius_cover_bottom>0</radius_cover_bottom><radius_cover_top>0</radius_cover_top>"
            "<radius_cover_thickness>0</radius_cover_thickness>"
        )
        xml += self.common_entity_xml("Profile", object_id, name, self.profile_uid, rotation, comment, color)
        self.objects.append(xml)
        self.profile_count += 1
        return object_id

    def add_panel(
        self,
        name: str,
        points_mm: Iterable[Point],
        comment: str = "",
        color: str = "#C6CCD2",
    ) -> int:
        object_id = self.next_id
        self.next_id += 1
        points = [tuple(cm(v) for v in point) for point in points_mm]
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

    def shelf_levels(self, height: float, shelf_count: int) -> list[float]:
        configured = self.spec.get("shelf_level_mm")
        if configured:
            return [float(item) for item in configured]
        bottom_clearance = float(self.spec.get("bottom_clearance_mm", 180))
        top_clearance = float(self.spec.get("top_clearance_mm", 160))
        if shelf_count == 1:
            return [height / 2.0]
        step = (height - bottom_clearance - top_clearance) / (shelf_count - 1)
        return [bottom_clearance + i * step for i in range(shelf_count)]

    def build(self) -> dict:
        dims = self.spec["finished_mm"]
        length = float(dims["length"])
        depth = float(dims["depth"])
        height = float(dims["height"])
        shelf_count = max(1, int(self.spec.get("shelf_count", self.spec.get("layers", 5))))
        bay_count = max(1, int(self.spec.get("bay_count", 2)))
        load_per_shelf = float(self.spec.get("load_per_shelf_kg", 40))
        p = self.profile_size
        half = p / 2.0
        inset = half

        if length <= 2 * p or depth <= 2 * p or height <= 2 * p:
            raise ValueError("finished dimensions are too small for the selected profile size")

        x_positions = [inset + i * (length - 2 * inset) / bay_count for i in range(bay_count + 1)]
        y_positions = [inset, depth - inset]
        levels = self.shelf_levels(height, shelf_count)
        perimeter_levels = sorted(set([half, height - half, *levels]))

        self.assumptions.append("finished_mm is treated as the finished outer shelf envelope")
        self.assumptions.append("4040 aluminum profile is used for posts, shelf frames, and bracing")
        self.assumptions.append("shelf boards are modeled as aluminum-colored 18 mm panel geometry")
        self.assumptions.append("shelf levels are adjustable in concept; exact hole pattern and hardware are not modeled")

        for x in x_positions:
            for y in y_positions:
                self.add_profile(f"vertical post X{x:.0f} Y{y:.0f}", "Z", (x, y, 0), height)

        for z in perimeter_levels:
            for y in y_positions:
                for bay in range(bay_count):
                    start_x = x_positions[bay] + half
                    end_x = x_positions[bay + 1] - half
                    self.add_profile(
                        f"front-back X beam bay {bay + 1} Y{y:.0f} Z{z:.0f}",
                        "X",
                        (start_x, y, z),
                        end_x - start_x,
                        f"{load_per_shelf:g} kg shelf support frame" if z in levels else "perimeter frame",
                    )
            for x in x_positions:
                self.add_profile(
                    f"depth beam X{x:.0f} Z{z:.0f}",
                    "Y",
                    (x, inset + half, z),
                    depth - 2 * inset - p,
                    f"{load_per_shelf:g} kg shelf support frame" if z in levels else "perimeter frame",
                )

        panel_x1, panel_x2 = p, length - p
        panel_y1, panel_y2 = p, depth - p
        for index, z in enumerate(levels, start=1):
            panel_z = min(height - self.panel_thickness / 2.0, z + half + self.panel_thickness / 2.0)
            self.add_panel(
                f"adjustable shelf panel {index}",
                [
                    (panel_x1, panel_y1, panel_z),
                    (panel_x2, panel_y1, panel_z),
                    (panel_x2, panel_y2, panel_z),
                    (panel_x1, panel_y2, panel_z),
                ],
                f"adjustable shelf panel, nominal load {load_per_shelf:g} kg",
            )

        if self.spec.get("include_diagonal_bracing", True):
            z_low = half
            z_high = height - half
            y_back = depth - inset
            self.add_profile_between(
                "rear diagonal brace rising",
                (inset, y_back, z_low),
                (length - inset, y_back, z_high),
                "rear anti-sway brace",
            )
            self.add_profile_between(
                "rear diagonal brace falling",
                (length - inset, y_back, z_low),
                (inset, y_back, z_high),
                "rear anti-sway brace",
            )
            self.add_profile_between(
                "left side diagonal brace",
                (inset, inset, z_low),
                (inset, depth - inset, z_high),
                "side anti-sway brace",
            )
            self.add_profile_between(
                "right side diagonal brace",
                (length - inset, inset, z_low),
                (length - inset, depth - inset, z_high),
                "side anti-sway brace",
            )

        return {
            "length": length,
            "depth": depth,
            "height": height,
            "shelf_count": shelf_count,
            "bay_count": bay_count,
            "shelf_levels_mm": levels,
            "load_per_shelf_kg": load_per_shelf,
        }

    def scene_xml(self, title: str, description: str) -> str:
        scene = '<?xml version="1.0" encoding="UTF-8" ?>\n<scene>'
        scene += (
            "<version>14</version><software_branch_uid>win_maytec_maycad_64</software_branch_uid>"
            "<software_version>12.10</software_version><isparamsample>0</isparamsample>"
            "<software_computer_id>codex-maycad-shelf</software_computer_id>"
            "<software_session_id>codex-maycad-shelf</software_session_id>"
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
            "<AuthorUUID><![CDATA[codex-maycad-shelf]]></AuthorUUID>"
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


def generate_three_views(spec: dict, built: dict, title: str) -> str:
    length, depth, height = built["length"], built["depth"], built["height"]
    levels = built["shelf_levels_mm"]
    bay_count = built["bay_count"]
    scale = min(780 / length, 420 / height)
    front_w, front_h = length * scale, height * scale
    top_h = depth * scale
    side_w, side_h = depth * scale, height * scale

    front = [svg_rect(0, 0, front_w, front_h)]
    for i in range(1, bay_count):
        x = front_w * i / bay_count
        front.append(svg_line(x, 0, x, front_h, "division"))
    for level in levels:
        y = front_h - level * scale
        front.append(svg_line(0, y, front_w, y, "shelf"))

    top = [svg_rect(0, 0, front_w, top_h)]
    for i in range(1, bay_count):
        x = front_w * i / bay_count
        top.append(svg_line(x, 0, x, top_h, "division"))

    side = [svg_rect(0, 0, side_w, side_h)]
    for level in levels:
        y = side_h - level * scale
        side.append(svg_line(0, y, side_w, y, "shelf"))

    assumptions = "".join(f"<li>{escape(item)}</li>" for item in spec.get("assumptions", []))
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
.shelf {{ stroke: #4b5563; stroke-width: 2.2; }}
.note {{ color: #4b5563; font-size: 14px; line-height: 1.5; }}
</style>
</head>
<body>
<main class="sheet">
<h1>{escape(title)}</h1>
<p class="note">Finished size: {fmt(length)} x {fmt(depth)} x {fmt(height)} mm. Shelves: {len(levels)}. Nominal load: {fmt(built["load_per_shelf_kg"])} kg per shelf.</p>
<section class="views">
<div class="view"><h2>Front view</h2><svg viewBox="-20 -28 {fmt(front_w + 80)} {fmt(front_h + 56)}">{''.join(front)}<text x="0" y="-8">L {fmt(length)} mm</text><text x="{fmt(front_w + 8)}" y="{fmt(front_h / 2)}">H {fmt(height)} mm</text></svg></div>
<div class="view"><h2>Top view</h2><svg viewBox="-20 -28 {fmt(front_w + 80)} {fmt(top_h + 56)}">{''.join(top)}<text x="0" y="-8">L {fmt(length)} mm</text><text x="{fmt(front_w + 8)}" y="{fmt(top_h / 2)}">D {fmt(depth)} mm</text></svg></div>
<div class="view"><h2>Side view</h2><svg viewBox="-20 -28 {fmt(side_w + 80)} {fmt(side_h + 56)}">{''.join(side)}<text x="0" y="-8">D {fmt(depth)} mm</text><text x="{fmt(side_w + 8)}" y="{fmt(side_h / 2)}">H {fmt(height)} mm</text></svg></div>
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
    project_name = safe_name(spec.get("project_name", "maycad_shelf"))
    title = spec.get("title", project_name.replace("_", " ").title())
    description = spec.get("description", "Generated aluminum-profile display shelf.")

    builder = ShelfSceneBuilder(spec)
    built = builder.build()
    spec["assumptions"] = builder.assumptions

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
        "assumptions": builder.assumptions,
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
