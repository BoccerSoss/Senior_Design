"""Generate a public two-sheet piping example with auditable synthetic records."""
from __future__ import annotations

import hashlib

from PIL import Image, ImageDraw, ImageFont

from .store import ident


SIZE = (1400, 850)
INK = "#203D50"
BLUE = "#176A83"
VIOLET = "#7758A5"


def _font(size, bold=False):
    try:
        return ImageFont.truetype("arialbd.ttf" if bold else "arial.ttf", size)
    except OSError:
        return ImageFont.load_default(size=size)


def _component(ident, tag, typ, x, y, *, normal="unknown", credit="unverified", role=None, orientation=None):
    width, height = (92, 92) if typ == "pump" else (88, 62)
    if typ == "tank":
        width, height = 106, 124
    elif typ == "junction":
        width = height = 18
    elif typ == "connector":
        width, height = 110, 54
    elif orientation == "vertical":
        width, height = 62, 88
    item = {"id": ident, "tag": tag, "type": typ,
            "bbox": [x - width // 2, y - height // 2, x + width // 2, y + height // 2],
            "normal_position": normal, "isolation_credit": credit, "review_status": "confirmed",
            "evidence": "Generated synthetic exercise equipment with a printed label."}
    if role:
        item["role"] = role
    if orientation:
        item["orientation"] = orientation
    if typ == "manual valve":
        item["actuation"] = "manual"
    return item


def _edge(ident, source, target, points, kind="pipe"):
    return {"id": ident, "source": source, "target": target, "kind": kind,
            "path": [list(point) for point in points], "review_status": "confirmed",
            "evidence": "Generated line joins the named symbol ports on this synthetic sheet."}


def _sheet_one(sheet_id, facility_id):
    components = [
        _component("s-101", "Supply tank", "tank", 130, 410, role="source"),
        _component("v-101", "V-101", "manual valve", 330, 410, normal="open", credit="eligible"),
        _component("p-101", "P-101", "pump", 550, 410),
        _component("j-101", "J-101", "junction", 760, 410),
        _component("v-102", "V-102", "manual valve", 1010, 240, normal="open", credit="eligible"),
        _component("t-102", "Local load", "tank", 1230, 240),
        _component("j-102", "J-102", "junction", 760, 550),
        _component("x-a", "X-A", "connector", 1230, 550),
        _component("dv-101", "DV-101", "drain valve", 550, 660, normal="closed", credit="excluded", role="drain", orientation="vertical"),
        _component("av-101", "AV-101", "vent valve", 550, 200, normal="closed", credit="excluded", role="vent", orientation="vertical"),
        _component("rv-101", "RV-101", "relief valve", 1030, 720, normal="closed", credit="excluded", role="relief"),
        _component("out-101", "Relief discharge", "open end", 1250, 720, role="open end"),
        _component("pi-101", "PI-101", "instrument", 250, 165),
    ]
    connections = [
        _edge("e-1", "s-101", "v-101", [(183, 410), (286, 410)]),
        _edge("e-2", "v-101", "p-101", [(374, 410), (504, 410)]),
        _edge("e-3", "p-101", "j-101", [(596, 410), (760, 410)]),
        _edge("e-4", "j-101", "v-102", [(760, 410), (760, 240), (966, 240)]),
        _edge("e-5", "v-102", "t-102", [(1054, 240), (1177, 240)]),
        _edge("e-6", "j-101", "j-102", [(760, 410), (760, 550)]),
        _edge("e-7", "j-102", "x-a", [(760, 550), (1175, 550)]),
        _edge("e-8", "p-101", "dv-101", [(550, 456), (550, 616)]),
        _edge("e-9", "p-101", "av-101", [(550, 364), (550, 244)]),
        _edge("e-10", "j-102", "rv-101", [(760, 550), (760, 720), (986, 720)]),
        _edge("e-11", "rv-101", "out-101", [(1074, 720), (1206, 720)]),
        _edge("signal-1", "p-101", "pi-101", [(515, 380), (425, 380), (425, 165), (281, 165)], "signal"),
    ]
    return _sheet(sheet_id, facility_id, "RL-001", "Research cooling loop and pump", 1, components, connections)


def _sheet_two(sheet_id, facility_id):
    components = [
        _component("x-b", "X-B", "connector", 140, 400),
        _component("j-201", "J-201", "junction", 350, 400),
        _component("v-201", "V-201", "manual valve", 650, 240, normal="closed", credit="eligible"),
        _component("t-201", "Heat exchanger return", "tank", 1200, 240),
        _component("cv-201", "CV-201", "check valve", 650, 560, credit="excluded"),
        _component("v-202", "V-202", "manual valve", 960, 560, normal="open", credit="eligible"),
        _component("t-202", "Second return", "tank", 1200, 560),
        _component("pi-201", "PI-201", "instrument", 920, 400),
    ]
    connections = [
        _edge("e-1", "x-b", "j-201", [(195, 400), (350, 400)]),
        _edge("e-2", "j-201", "v-201", [(350, 400), (350, 240), (606, 240)]),
        _edge("e-3", "v-201", "t-201", [(694, 240), (1147, 240)]),
        _edge("e-4", "j-201", "cv-201", [(350, 400), (350, 560), (606, 560)]),
        _edge("e-5", "cv-201", "v-202", [(694, 560), (916, 560)]),
        _edge("e-6", "v-202", "t-202", [(1004, 560), (1147, 560)]),
        _edge("signal-1", "v-201", "pi-201", [(650, 205), (800, 205), (800, 400), (889, 400)], "signal"),
    ]
    return _sheet(sheet_id, facility_id, "RL-002", "Parallel return branches", 2, components, connections)


def _sheet(sheet_id, facility_id, number, title, page, components, connections):
    return {"id": sheet_id, "facility_id": facility_id, "title": title, "drawing_number": number,
            "sheet_number": str(page), "revision": "A", "filename": f"{number}.png", "source_page": 1,
            "width": SIZE[0], "height": SIZE[1], "image_url": f"/api/images/{sheet_id}",
            "grid": {"columns": 14, "rows": 10, "origin": "top-left"},
            "components": components, "connections": connections,
            "source_metadata": {"synthetic": True, "demo_kind": "two-sheet", "demo_sheet": page,
                                "purpose": "Engineering review exercise with deliberate unbounded special paths."}}


def _arrow(draw, tip, direction="right", color=BLUE):
    x, y = tip
    if direction == "right":
        points = [(x, y), (x - 12, y - 6), (x - 12, y + 6)]
    else:
        points = [(x, y), (x - 6, y - 12), (x + 6, y - 12)]
    draw.polygon(points, fill=color)


def _render(sheet, path):
    image = Image.new("RGB", SIZE, "#FFFFFF")
    draw = ImageDraw.Draw(image)
    body, tag, heading = _font(18), _font(22, True), _font(30, True)
    draw.text((46, 28), sheet["title"], font=heading, fill=INK)
    draw.text((46, 69), "Synthetic research loop  /  Equipment and process connections", font=body, fill="#586E7C")
    draw.text((SIZE[0] - 240, 39), f"{sheet['drawing_number']}   Rev A", font=tag, fill=INK)
    draw.line((46, 106, 1354, 106), fill="#DAE2E7", width=2)
    for edge in sheet["connections"]:
        points = [tuple(point) for point in edge["path"]]
        if edge["kind"] == "signal":
            for first, last in zip(points, points[1:]):
                length = max(abs(last[0] - first[0]), abs(last[1] - first[1]))
                for start in range(0, length, 17):
                    end = min(start + 9, length)
                    a = tuple(round(x + (z - x) * start / max(1, length)) for x, z in zip(first, last))
                    b = tuple(round(x + (z - x) * end / max(1, length)) for x, z in zip(first, last))
                    draw.line((a, b), fill=VIOLET, width=3)
        else:
            draw.line(points, fill=BLUE, width=4, joint="curve")
    for item in sheet["components"]:
        x0, y0, x1, y1 = item["bbox"]
        x, y = (x0 + x1) // 2, (y0 + y1) // 2
        typ = item["type"]
        if typ == "pump":
            draw.ellipse((x0, y0, x1, y1), fill="white", outline=INK, width=4)
            draw.polygon(((x - 20, y - 24), (x + 26, y), (x - 20, y + 24)), outline=INK, fill="#EAF2F5", width=3)
        elif typ == "tank":
            draw.rounded_rectangle((x0, y0, x1, y1), radius=22, fill="white", outline=INK, width=3)
            draw.line((x0 + 6, y + 8, x1 - 6, y + 8), fill=BLUE, width=2)
        elif typ == "junction":
            draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill=BLUE)
        elif typ == "connector":
            draw.polygon(((x0, y0), (x1 - 18, y0), (x1, y), (x1 - 18, y1), (x0, y1)),
                         fill="#EDF5F8", outline=INK, width=3)
            draw.text((x - 23, y - 13), item["tag"], font=tag, fill=INK)
        elif typ == "instrument":
            draw.ellipse((x - 31, y - 31, x + 31, y + 31), fill="white", outline=VIOLET, width=3)
            draw.text((x - 11, y - 12), "PI", font=body, fill=VIOLET)
        elif typ == "open end":
            draw.line((x0, y, x1 - 12, y), fill=BLUE, width=4)
            _arrow(draw, (x1, y))
        elif typ == "check valve":
            draw.rectangle((x0, y0, x1, y1), fill="white")
            draw.line((x0, y, x - 24, y, x + 22, y + 22, x + 22, y - 22), fill=INK, width=4)
            draw.line((x + 22, y, x1, y), fill=INK, width=4)
            draw.ellipse((x - 28, y - 4, x - 20, y + 4), fill=INK)
        else:
            draw.rectangle((x0 + 3, y0 + 4, x1 - 3, y1 - 4), fill="white")
            if item.get("orientation") == "vertical":
                draw.polygon(((x - 22, y - 34), (x + 22, y + 34), (x - 22, y + 34), (x + 22, y - 34)),
                             fill="#F6F9FB", outline=INK, width=3)
                draw.line((x, y0, x, y - 34), fill=INK, width=3)
                draw.line((x, y + 34, x, y1), fill=INK, width=3)
                draw.line((x + 23, y, x + 35, y), fill=INK, width=3)
                draw.line((x + 35, y - 14, x + 35, y + 14), fill=INK, width=3)
            else:
                draw.polygon(((x - 34, y - 22), (x + 34, y + 22), (x + 34, y - 22), (x - 34, y + 22)),
                             fill="#F6F9FB", outline=INK, width=3)
                draw.line((x0, y, x - 34, y), fill=INK, width=3)
                draw.line((x + 34, y, x1, y), fill=INK, width=3)
                draw.line((x, y - 23, x, y - 35), fill=INK, width=3)
                draw.line((x - 14, y - 35, x + 14, y - 35), fill=INK, width=3)
        if typ != "connector":
            label = item["tag"]
            text_box = draw.textbbox((0, 0), label, font=tag)
            label_at = (x - (text_box[2] - text_box[0]) / 2, y1 + 13)
            if item.get("orientation") == "vertical":
                label_at = (x + 65, y - 12)
            elif typ == "pump":
                label_at = (x + 70, y + 42)
            elif typ == "junction":
                label_at = (x + 16, y + 9)
            draw.text(label_at, label, font=tag, fill=INK)
        if item["normal_position"] == "closed":
            note_at = (x + 65, y - 36) if item.get("orientation") == "vertical" else (x - 45, y0 - 58)
            draw.text(note_at, "Normally closed", font=_font(15), fill="#5D6F78")
    footer = ("X-A continues on RL-002 / X-B. Relief, drain and vent paths remain review items."
              if sheet["sheet_number"] == "1" else
              "X-B continues from RL-001 / X-A. CV-201 is a check valve and receives no isolation credit.")
    draw.line((46, 794, 1354, 794), fill="#DAE2E7", width=2)
    draw.text((46, 810), footer, font=_font(16), fill="#586E7C")
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def create_demo(store, kind="two-sheet"):
    """Create or reopen the same generated two-sheet research loop."""
    if kind != "two-sheet":
        raise ValueError(f"Unknown demo {kind!r}. Choose the two-sheet research loop.")
    state = store.snapshot()
    existing = [sheet for sheet in state["sheets"]
                if sheet.get("source_metadata", {}).get("demo_kind") == kind]
    facility_id = existing[0]["facility_id"] if existing else store.add_facility("Synthetic research loop")["id"]
    sheet_ids = []
    created = False
    for page, builder in ((1, _sheet_one), (2, _sheet_two)):
        match = next((sheet for sheet in existing if sheet["facility_id"] == facility_id
                      and sheet["source_metadata"].get("demo_sheet") == page), None)
        if match:
            sheet_ids.append(match["id"])
            continue
        created = True
        sheet_id = ident("sheet")
        sheet = builder(sheet_id, facility_id)
        image_path = store.directory / "images" / f"{sheet_id}.png"
        _render(sheet, image_path)
        sheet["source_sha256"] = hashlib.sha256(image_path.read_bytes()).hexdigest()
        stored = store.add_sheet(sheet)["sheet"]
        sheet_ids.append(stored["id"])
    state = store.snapshot()
    ends = [{"sheet_id": sheet_ids[0], "component_id": "x-a"},
            {"sheet_id": sheet_ids[1], "component_id": "x-b"}]
    if not any(link.get("from") in ends and link.get("to") in ends for link in state["links"]):
        store.save_link({"from": ends[0], "to": ends[1], "status": "confirmed",
                         "evidence": "The two generated continuation labels identify each other explicitly."})
    return {"facility_id": facility_id, "sheet_ids": sheet_ids,
            "target": {"sheet_id": sheet_ids[0], "component_id": "p-101"},
            "synthetic": True, "duplicate": not created,
            "title": "Synthetic research loop"}
