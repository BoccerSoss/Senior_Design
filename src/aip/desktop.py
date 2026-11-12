"""Desktop view for running the existing local drawing analysis."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import tkinter as tk
from tkinter import filedialog, font, messagebox, ttk

from PIL import Image, ImageTk

from .desktop_analysis import AnalysisError, analyze_drawing
from .io import ROOT, image_info

SAMPLE = ROOT / "data" / "input" / "four_component.png"
FLOW_SAMPLE = ROOT / "data" / "input" / "four_component_flow.png"
SYMBOL_REFERENCE = ROOT / "docs" / "symbol_reference.html"
PAGE = "#edf2f5"
PANEL = "#ffffff"
INK = "#172c39"
MUTED = "#506574"
ACCENT = "#116a57"
LINE = "#d8e2e7"
STRIPE = "#f3f7f8"
ERROR = "#a12838"
COORDINATE_HINT = "Click the drawing for coordinates."


@dataclass(frozen=True)
class PreviewGeometry:
    left: int
    top: int
    width: int
    height: int
    image_width: int
    image_height: int

    def image_point(self, x, y):
        x, y = x - self.left, y - self.top
        if 0 <= x < self.width and 0 <= y < self.height:
            return int(x * self.image_width / self.width), int(y * self.image_height / self.height)
        return None

    def canvas_point(self, point):
        x, y = point
        return (self.left + (x + 0.5) * self.width / self.image_width,
                self.top + (y + 0.5) * self.height / self.image_height)


def _segment_distance_squared(point, start, end):
    dx, dy = end[0] - start[0], end[1] - start[1]
    px, py = point[0] - start[0], point[1] - start[1]
    length_squared = dx * dx + dy * dy
    along = min(1, max(0, (px * dx + py * dy) / length_squared)) if length_squared else 0
    return (px - dx * along) ** 2 + (py - dy * along) ** 2


class DesktopApp:
    def __init__(self, root, output_root=ROOT / "runs" / "desktop"):
        self.root = root
        self.output_root = Path(output_root)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="drawing-analysis")
        self.future = None
        self.result = None
        self.run_folder = None
        self.source_path = None
        self.source_image = None
        self.overlay_image = None
        self.preview_photo = None
        self.preview_geometry = None
        self.coordinate_point = None
        self.coordinate_text = tk.StringVar(root)
        self.drawing_hint = tk.StringVar(root, COORDINATE_HINT)
        self.show_detections = tk.BooleanVar(root, False)
        self.filename = tk.StringVar(root)
        self.image_detail = tk.StringVar(root)
        self.status = tk.StringVar(root)
        self.matrix_detail = tk.StringVar(root)
        self.reference_detail = tk.StringVar(root)
        self.component_marks = {}
        self.connection_marks = {}
        self.root.title("P&ID Matrix Builder")
        max_width, max_height = self.root.maxsize()
        self.root.geometry(f"{min(1120, max_width)}x{min(780, max_height)}")
        self.root.minsize(min(900, max_width), min(680, max_height))
        self.root.configure(background=PAGE)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.configure_styles()
        self.build()
        self.load_drawing(SAMPLE)

    def configure_styles(self):
        families = font.families(self.root)
        family = "Inter" if "Inter" in families else "Segoe UI"
        self.body_font = (family, 10)
        self.heading_font = (family, 11, "bold")
        self.table_font = font.Font(self.root, family=family, size=10)
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background=PAGE)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=PAGE, foreground=INK, font=self.body_font)
        style.configure("Title.TLabel", background=PAGE, foreground=INK, font=(family, 25, "bold"))
        style.configure("Subtitle.TLabel", background=PAGE, foreground=MUTED, font=self.body_font)
        style.configure("Panel.TLabel", background=PANEL, foreground=INK, font=self.body_font)
        style.configure("PanelHeading.TLabel", background=PANEL, foreground=INK, font=self.heading_font)
        style.configure("PanelMuted.TLabel", background=PANEL, foreground=MUTED, font=self.body_font)
        style.configure("Status.TLabel", background=PAGE, foreground=MUTED, font=self.body_font)
        style.configure("Error.TLabel", background=PAGE, foreground=ERROR, font=self.body_font)
        style.configure("TButton", font=self.heading_font, padding=(10, 4),
                        background=PANEL, foreground=INK, bordercolor=LINE)
        style.map("TButton", background=[("active", STRIPE)], foreground=[("disabled", MUTED)])
        style.configure("Analyze.TButton", background=ACCENT, foreground=PANEL, bordercolor=ACCENT,
                        focuscolor=PANEL, padding=(16, 7))
        style.map("Analyze.TButton", background=[("disabled", LINE), ("active", "#0b5142")],
                  foreground=[("disabled", MUTED), ("!disabled", PANEL)])
        style.configure("Preview.TCheckbutton", background=PANEL, foreground=INK, font=self.body_font)
        style.map("Preview.TCheckbutton", background=[("active", PANEL)])
        style.configure("Treeview", rowheight=26, background=PANEL, fieldbackground=PANEL,
                        foreground=INK, bordercolor=LINE, font=self.body_font)
        style.configure("Treeview.Heading", background=STRIPE, foreground=INK,
                        bordercolor=LINE, font=self.heading_font, padding=(7, 3))
        style.map("Treeview", background=[("selected", "#dbeee8")], foreground=[("selected", INK)])
        style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=LINE,
                        bordercolor=LINE, lightcolor=ACCENT, darkcolor=ACCENT)

    def build(self):
        root = self.root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        header = ttk.Frame(root)
        header.grid(row=0, column=0, sticky="ew", padx=22, pady=(14, 9))
        ttk.Button(header, text="Symbol reference", command=self.open_symbol_reference).pack(side="right")
        ttk.Label(header, text="P&ID Matrix Builder", style="Title.TLabel").pack(anchor="w")
        ttk.Label(header, text="Turn a piping drawing into a connection matrix.",
                  style="Subtitle.TLabel").pack(anchor="w", pady=(2, 0))

        toolbar = ttk.Frame(root, style="Panel.TFrame", padding=(13, 8))
        toolbar.grid(row=1, column=0, sticky="ew", padx=22, pady=(0, 12))
        toolbar.columnconfigure(0, weight=1)
        ttk.Label(toolbar, textvariable=self.filename, style="PanelHeading.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(toolbar, textvariable=self.image_detail, style="PanelMuted.TLabel").grid(row=1, column=0, sticky="w")
        self.sample_button = ttk.Menubutton(toolbar, text="Samples")
        sample_menu = tk.Menu(self.sample_button, tearoff=False)
        sample_menu.add_command(label="Four equipment items", command=lambda: self.load_drawing(SAMPLE))
        sample_menu.add_command(label="Flow arrows", command=lambda: self.load_drawing(FLOW_SAMPLE))
        self.sample_button.configure(menu=sample_menu)
        self.sample_button.grid(row=0, column=1, rowspan=2, padx=(10, 6))
        self.open_button = ttk.Button(toolbar, text="Open drawing…", command=self.open_drawing)
        self.open_button.grid(row=0, column=2, rowspan=2, padx=(0, 9))
        self.analyze_button = ttk.Button(toolbar, text="Analyze drawing", command=self.analyze, style="Analyze.TButton")
        self.analyze_button.grid(row=0, column=3, rowspan=2)

        workspace = ttk.Frame(root)
        workspace.grid(row=2, column=0, sticky="nsew", padx=22)
        workspace.columnconfigure(0, weight=1, uniform="work")
        workspace.columnconfigure(1, weight=1, uniform="work")
        workspace.rowconfigure(0, weight=1)

        drawing = ttk.Frame(workspace, style="Panel.TFrame", padding=13)
        drawing.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        drawing.columnconfigure(0, weight=1)
        drawing.rowconfigure(1, weight=1)
        ttk.Label(drawing, text="Drawing", style="PanelHeading.TLabel").grid(row=0, column=0, sticky="w")
        self.overlay_toggle = ttk.Checkbutton(drawing, text="Show detections", variable=self.show_detections,
                                              command=self.redraw, style="Preview.TCheckbutton")
        self.overlay_toggle.grid(row=0, column=1, sticky="e")
        self.canvas = tk.Canvas(drawing, background=PANEL, highlightthickness=1,
                                highlightbackground=LINE, width=360, height=220)
        self.canvas.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
        self.canvas.bind("<Configure>", self.redraw)
        self.canvas.bind("<Button-1>", self.inspect_drawing)
        ttk.Label(drawing, textvariable=self.drawing_hint,
                  style="PanelMuted.TLabel").grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.coordinate_popup = ttk.Frame(self.canvas, padding=(10, 8), style="Panel.TFrame",
                                          relief="solid", borderwidth=1)
        self.coordinate_popup.columnconfigure(0, weight=1)
        ttk.Label(self.coordinate_popup, text="Drawing coordinates", style="PanelHeading.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 10))
        ttk.Button(self.coordinate_popup, text="Close", command=self.hide_coordinates).grid(row=0, column=1)
        self.coordinate_label = ttk.Label(self.coordinate_popup, textvariable=self.coordinate_text,
                                          justify="left", style="Panel.TLabel")
        self.coordinate_label.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        root.bind("<Escape>", self.hide_coordinates)

        results = ttk.Frame(workspace, style="Panel.TFrame", padding=13)
        results.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        results.columnconfigure(0, weight=1)
        results.rowconfigure(2, weight=1, minsize=158)
        results.rowconfigure(4, weight=3, minsize=115)
        title_row = ttk.Frame(results, style="Panel.TFrame")
        title_row.grid(row=0, column=0, sticky="ew")
        title_row.columnconfigure(0, weight=1)
        ttk.Label(title_row, text="Connection matrix", style="PanelHeading.TLabel").grid(row=0, column=0, sticky="w")
        self.save_button = ttk.Button(title_row, text="Save matrix…", command=self.save_matrix)
        self.save_button.grid(row=0, column=1, sticky="e")
        ttk.Label(results, text="1 = direct connection    0 = no direct connection",
                  style="PanelMuted.TLabel").grid(row=1, column=0, sticky="w", pady=(5, 7))
        matrix_frame, self.matrix = self.table(results, height=4, horizontal=True)
        matrix_frame.grid(row=2, column=0, sticky="nsew")
        self.matrix.bind("<ButtonRelease-1>", self.inspect_cell)
        ttk.Label(results, textvariable=self.matrix_detail, style="PanelMuted.TLabel",
                  wraplength=370).grid(row=3, column=0, sticky="w", pady=(6, 9))

        observations = ttk.Frame(results, style="Panel.TFrame")
        observations.grid(row=4, column=0, sticky="nsew")
        observations.columnconfigure(0, weight=2, uniform="observations")
        observations.columnconfigure(1, weight=3, uniform="observations")
        observations.rowconfigure(1, weight=1)
        self.equipment_title = ttk.Label(observations, text="Equipment", style="PanelHeading.TLabel")
        self.equipment_title.grid(row=0, column=0, sticky="w", pady=(0, 5))
        self.connections_title = ttk.Label(observations, text="Direct connections", style="PanelHeading.TLabel")
        self.connections_title.grid(row=0, column=1, sticky="w", padx=(6, 0), pady=(0, 5))
        eq_frame, self.equipment = self.table(observations, height=4, horizontal=True)
        eq_frame.grid(row=1, column=0, sticky="nsew", padx=(0, 5))
        self.columns(self.equipment, ("Tag", "Type"), stretch=True)
        self.equipment.bind("<<TreeviewSelect>>", self.inspect_equipment)
        cn_frame, self.connections = self.table(observations, height=4, horizontal=True)
        cn_frame.grid(row=1, column=1, sticky="nsew", padx=(6, 0))
        self.columns(self.connections, ("Tag A", "Tag B", "Flow"), stretch=True)
        self.connections.bind("<<TreeviewSelect>>", self.inspect_connection)
        self.reference_label = ttk.Label(results, textvariable=self.reference_detail,
                                         style="PanelMuted.TLabel", wraplength=370)
        self.reference_label.grid(row=5, column=0, sticky="w", pady=(8, 0))

        footer = ttk.Frame(root)
        footer.grid(row=3, column=0, sticky="ew", padx=22, pady=(9, 13))
        footer.columnconfigure(0, weight=1)
        self.status_label = ttk.Label(footer, textvariable=self.status, style="Status.TLabel", wraplength=440)
        self.status_label.grid(row=0, column=0, sticky="w")
        self.progress = ttk.Progressbar(footer, mode="indeterminate", length=85)
        self.progress.grid(row=0, column=1, padx=8)
        self.progress.grid_remove()
        self.markings_button = ttk.Button(footer, text="Save markings…", command=self.save_markings)
        self.markings_button.grid(row=0, column=2, sticky="e", padx=(0, 7))
        self.folder_button = ttk.Button(footer, text="Open results folder", command=self.open_results)
        self.folder_button.grid(row=0, column=3, sticky="e")

    def table(self, parent, height, horizontal=False):
        frame = ttk.Frame(parent, style="Panel.TFrame")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        tree = ttk.Treeview(frame, show="headings", height=height, selectmode="browse")
        tree.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=vertical.set)
        if horizontal:
            scrollbar = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
            scrollbar.grid(row=1, column=0, sticky="ew")
            tree.configure(xscrollcommand=scrollbar.set)
        tree.tag_configure("alternate", background=STRIPE)
        return frame, tree

    def columns(self, tree, labels, stretch=False, rows=()):
        ids = [f"col{index}" for index in range(len(labels))]
        widths = [max(69, self.table_font.measure(label) + 23) for label in labels]
        for row in rows:
            for index, value in enumerate(row):
                widths[index] = max(widths[index], self.table_font.measure(str(value)) + 20)
        tree.configure(columns=ids)
        for index, (column, label, width) in enumerate(zip(ids, labels, widths)):
            anchor = "w" if stretch or index == 0 else "center"
            tree.heading(column, text=label, anchor=anchor)
            tree.column(column, width=width, minwidth=55, stretch=stretch, anchor=anchor)

    @staticmethod
    def fill_table(tree, rows):
        children = tree.get_children()
        if children:
            tree.delete(*children)
        for index, values in enumerate(rows):
            tree.insert("", "end", iid=str(index), values=values, tags=("alternate",) if index % 2 else ())
        tree.yview_moveto(0)
        tree.xview_moveto(0)

    def set_status(self, message, error=False):
        self.status.set(message)
        self.status_label.configure(style="Error.TLabel" if error else "Status.TLabel")

    def refresh_buttons(self):
        busy = self.future is not None
        self.sample_button.configure(state="disabled" if busy else "normal")
        self.open_button.configure(state="disabled" if busy else "normal")
        self.analyze_button.configure(state="normal" if self.source_path and not busy else "disabled")
        self.overlay_toggle.configure(state="normal" if self.overlay_image is not None and not busy else "disabled")
        self.save_button.configure(state="normal" if self.result and not busy else "disabled")
        self.markings_button.configure(state="normal" if self.result and self.result.markings_file and not busy else "disabled")
        self.folder_button.configure(state="normal" if self.run_folder and not busy else "disabled")

    def clear_results(self):
        self.hide_coordinates()
        self.drawing_hint.set(COORDINATE_HINT)
        self.result = None
        self.run_folder = None
        self.overlay_image = None
        self.component_marks.clear()
        self.connection_marks.clear()
        self.show_detections.set(False)
        self.fill_table(self.matrix, ())
        self.columns(self.matrix, ("Tag",), stretch=True)
        self.fill_table(self.equipment, ())
        self.fill_table(self.connections, ())
        self.equipment_title.configure(text="Equipment")
        self.connections_title.configure(text="Direct connections")
        self.matrix_detail.set("Select Analyze drawing to build the matrix.")
        self.reference_detail.set("")

    def load_drawing(self, path):
        if self.future is not None:
            return
        try:
            source = image_info(path)
        except (ValueError, OSError) as exc:
            self.set_status(f"Could not open {Path(path).name}. {exc}", error=True)
            self.refresh_buttons()
            return
        self.source_path = Path(path).resolve()
        self.source_image = source
        self.clear_results()
        name = self.source_path.name
        self.filename.set(name if len(name) <= 50 else name[:46] + "…")
        kind = "Synthetic example" if self.source_path in {SAMPLE, FLOW_SAMPLE} else "Selected drawing"
        self.image_detail.set(f"{kind}  ·  {source.width} × {source.height} pixels")
        self.set_status("Ready to analyze")
        self.refresh_buttons()
        self.redraw()

    def open_drawing(self):
        path = filedialog.askopenfilename(parent=self.root, title="Open a piping drawing",
                                          filetypes=[("Drawing images", ("*.png", "*.jpg", "*.jpeg"))])
        if path:
            self.load_drawing(path)

    def redraw(self, _event=None):
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        source = self.overlay_image if self.show_detections.get() and self.overlay_image is not None else self.source_image
        if source is None or width < 4 or height < 4:
            self.preview_geometry = None
            return
        scaled = source.copy()
        scaled.thumbnail((max(1, width - 20), max(1, height - 20)), Image.Resampling.LANCZOS)
        self.preview_photo = ImageTk.PhotoImage(scaled, master=self.root)
        self.canvas.delete("preview")
        left, top = (width - scaled.width) // 2, (height - scaled.height) // 2
        self.preview_geometry = PreviewGeometry(left, top, scaled.width, scaled.height, source.width, source.height)
        self.canvas.create_image(left, top, anchor="nw", image=self.preview_photo, tags="preview")
        self.place_coordinates()

    def show_coordinates(self, point, details=(), show_pixel=True):
        self.coordinate_point = point
        lines = list(details)
        if point is not None and show_pixel:
            lines.insert(0, f"Pixel x = {point[0]}, y = {point[1]}")
        self.coordinate_text.set("\n".join(lines))
        self.place_coordinates()

    def place_coordinates(self):
        self.canvas.delete("coordinate-point")
        if not self.coordinate_text.get() or self.preview_geometry is None:
            self.coordinate_popup.place_forget()
            return
        width = self.canvas.winfo_width()
        left = False
        if self.coordinate_point is not None:
            x, y = self.preview_geometry.canvas_point(self.coordinate_point)
            self.canvas.create_oval(x - 5, y - 5, x + 5, y + 5, outline=PANEL, width=4, tags="coordinate-point")
            self.canvas.create_oval(x - 5, y - 5, x + 5, y + 5, outline=ACCENT, width=2, tags="coordinate-point")
            left = x > width / 2
        self.coordinate_label.configure(wraplength=max(100, min(310, width - 44)))
        self.coordinate_popup.place(relx=0 if left else 1, x=10 if left else -10, y=10,
                                    anchor="nw" if left else "ne")

    def hide_coordinates(self, _event=None):
        self.coordinate_point = None
        self.coordinate_text.set("")
        self.coordinate_popup.place_forget()
        self.canvas.delete("coordinate-point")

    def mark_at(self, canvas_point, image_point):
        if not self.result or not self.show_detections.get():
            return None
        x, y = image_point
        markings = self.result.markings or {}
        best, best_key = None, None
        for mark in reversed(markings.get("marks", [])):
            box = mark.get("bbox")
            inside = box is not None and box[0] <= x < box[2] and box[1] <= y < box[3]
            points = [self.preview_geometry.canvas_point(point) for point in mark.get("points", ())]
            distance = 0 if inside else min((_segment_distance_squared(canvas_point, start, end)
                                             for start, end in zip(points, points[1:])), default=float("inf"))
            if distance > 6 ** 2:
                continue
            priority = {"observed_arrow": 2, "flow_arrow": 2, "flow_route": 1}.get(mark["kind"], 0)
            key = (-priority, distance)
            if best_key is None or key < best_key:
                best, best_key = mark, key
        return best

    def inspect_drawing(self, event):
        if self.preview_geometry is None:
            return
        canvas_point = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        point = self.preview_geometry.image_point(*canvas_point)
        if point is None:
            self.hide_coordinates()
            return
        mark = self.mark_at(canvas_point, point) or {}
        kind = mark.get("kind")
        if kind in {"observed_arrow", "flow_route"} or (kind == "flow_arrow" and mark.get("route_id")):
            self.show_coordinates(point, self.route_coordinates(mark))
            return
        component_id = mark.get("component_id")
        if component_id:
            record = next((item for item in self.component_marks.values() if item["id"] == component_id), None)
            if record is not None:
                self.show_equipment(record["label"], record["type"], point)
                return
        connection = mark.get("connection")
        if connection:
            record = next((item for item in self.connection_marks.values()
                           if [item["source"], item["target"]] == connection), None)
            if record is not None:
                self.show_connection(record["source_label"], record["target_label"], point)
                return
        self.show_coordinates(point)

    def route_coordinates(self, mark):
        marks = (self.result.markings or {}).get("marks", [])
        route_id = mark.get("route_id")
        route = mark if mark["kind"] == "flow_route" else None
        arrow = mark if mark["kind"] in {"observed_arrow", "flow_arrow"} else None
        if route_id:
            if route is None:
                route = next((m for m in marks if m["kind"] == "flow_route" and m.get("route_id") == route_id), None)
            if arrow is None:
                arrow = next((m for m in marks if m["kind"] in {"observed_arrow", "flow_arrow"}
                              and m.get("route_id") == route_id), None)
        lines = []
        if arrow is not None:
            wing_a, tip, wing_b = arrow["points"]
            direction = arrow.get("direction", "unknown").replace("_", " ")
            lines.extend((f"Flow arrow · {direction}", f"Arrow tip {tip} px", f"Arrow wings {wing_a}, {wing_b} px"))
        if route is not None:
            points = route["points"]
            lines.append(f"Flow route {points[0]} to {points[-1]} px")
            labels = route.get("component_labels")
            if labels is None:
                labels_by_id = {record["id"]: record["label"] for record in self.component_marks.values()}
                labels = [labels_by_id.get(component_id, component_id) for component_id in route.get("component_ids", [])]
            if labels:
                through = " → ".join(labels[:4])
                if len(labels) > 4:
                    through += f" + {len(labels) - 4} more"
                lines.append(f"Route equipment {through}")
        elif arrow is not None:
            lines.append("Equipment route unresolved")
        return lines

    def analyze(self):
        if self.source_path is None or self.future is not None:
            return
        self.clear_results()
        self.matrix_detail.set("Reading tags and tracing direct connections…")
        self.set_status("Analyzing drawing…")
        self.progress.grid()
        self.progress.start(16)
        self.future = self.executor.submit(analyze_drawing, self.source_path, self.output_root)
        self.refresh_buttons()
        self.redraw()
        self.root.after(100, self.poll_result)

    def poll_result(self):
        if not self.future.done():
            self.root.after(100, self.poll_result)
            return
        future, self.future = self.future, None
        self.progress.stop()
        self.progress.grid_remove()
        try:
            result = future.result()
            self.display_result(result)
        except AnalysisError as exc:
            self.run_folder = exc.directory
            self.matrix_detail.set("Analysis failed. Select Analyze drawing to retry.")
            self.set_status(exc.diagnostic, error=True)
        except (ValueError, OSError) as exc:
            self.matrix_detail.set("The results could not be displayed.")
            self.set_status(str(exc), error=True)
        self.refresh_buttons()

    def display_result(self, result):
        self.run_folder = result.directory
        self.source_image = image_info(result.image)
        self.overlay_image = image_info(result.overlay) if result.overlay else None
        self.result = result
        markings = result.markings or {}
        arrows = sum(mark["kind"] in {"observed_arrow", "flow_arrow"} for mark in markings.get("marks", []))
        routes = len(markings.get("flow_routes", []))
        arrow_text = f"{arrows} visible arrow{'s' if arrows != 1 else ''}"
        route_text = f"{routes} traced route{'s' if routes != 1 else ''}"
        self.drawing_hint.set(f"{COORDINATE_HINT}\n{arrow_text}  ·  {route_text}")
        self.component_marks = {record["label"]: record for record in markings.get("components", [])}
        self.connection_marks = {
            frozenset((record["source_label"], record["target_label"])): record
            for record in markings.get("connections", [])
        }
        kind = "Synthetic example" if self.source_path in {SAMPLE, FLOW_SAMPLE} else "Selected drawing"
        self.image_detail.set(f"{kind}  ·  {self.source_image.width} × {self.source_image.height} pixels")
        self.show_detections.set(self.overlay_image is not None)
        self.columns(self.matrix, ("Tag", *result.matrix_labels), stretch=not result.matrix_labels,
                     rows=[(label,) for label in result.matrix_labels])
        self.fill_table(self.matrix, [(label, *values) for label, values in zip(result.matrix_labels, result.matrix)])
        self.columns(self.equipment, ("Tag", "Type"), rows=result.equipment)
        connection_rows = [(a, b, self.flow_label(a, b)) for a, b in result.connections]
        self.columns(self.connections, ("Tag A", "Tag B", "Flow"), rows=connection_rows)
        self.fill_table(self.equipment, result.equipment)
        self.fill_table(self.connections, connection_rows)
        self.equipment_title.configure(text=f"Equipment ({len(result.equipment)})")
        self.connections_title.configure(text=f"Direct connections ({len(result.connections)})")
        if not result.equipment:
            self.matrix_detail.set("No equipment detected in this drawing.")
        elif not result.connections:
            self.matrix_detail.set("No direct connections detected.")
        else:
            self.matrix_detail.set("Select an equipment row, connection, or matrix cell to inspect it.")
        if result.evaluation is not None:
            score = result.evaluation["connections"]["f1"]
            score_text = f"{score:.2f}" if score is not None else "unavailable"
            self.reference_detail.set(f"Synthetic reference comparison  ·  Connection F1 {score_text} (provisional)")
        elif result.equipment and self.source_path != FLOW_SAMPLE:
            self.reference_detail.set("Review candidate detections against the drawing.")
        equipment_name = "equipment items" if self.source_path in {SAMPLE, FLOW_SAMPLE} else "equipment candidates"
        self.set_status(f"Analysis complete  ·  {len(result.equipment)} {equipment_name}  ·  {len(result.connections)} direct connections")
        self.redraw()

    def inspect_cell(self, event):
        if self.result is None:
            return
        row = self.matrix.identify_row(event.y)
        column = self.matrix.identify_column(event.x)
        if not row or not column or int(column[1:]) < 2:
            return
        i, j = int(row), int(column[1:]) - 2
        labels = self.result.matrix_labels
        if i == j:
            self.hide_coordinates()
            self.matrix_detail.set(f"{labels[i]} is the same equipment in this row and column.")
        else:
            if self.result.matrix[i][j]:
                self.show_connection(labels[i], labels[j])
            else:
                self.hide_coordinates()
                self.matrix_detail.set(f"{labels[i]} ↔ {labels[j]}  ·  No direct connection (0)")

    def flow_label(self, a, b):
        record = self.connection_marks.get(frozenset((a, b)))
        if record is None or record["direction"]["status"] == "unknown":
            return "Unknown"
        destination = record["target_label"] if record["direction"]["to"] == record["target"] else record["source_label"]
        return "A → B" if destination == b else "B → A"

    def show_connection(self, a, b, point=None):
        flow = self.flow_label(a, b)
        flow_text = {"Unknown": "Flow unknown", "A → B": f"Flow {a} → {b}", "B → A": f"Flow {b} → {a}"}[flow]
        record = self.connection_marks.get(frozenset((a, b)), {})
        path = record.get("path")
        direction = record.get("direction", {})
        tip = direction.get("arrow_tip")
        detail = f"{a} ↔ {b}  ·  Direct connection (1)  ·  {flow_text}"
        popup = [f"{a} ↔ {b}", flow_text]
        if path:
            popup.append(f"Pipe {path[0]} to {path[-1]} px")
        if tip is not None:
            tip_text = f"Arrow tip {tip} px"
            popup.append(tip_text)
            popup.append(f"Arrow wings {direction['arrow_wings']} px")
            detail += f"  ·  {tip_text}"
        elif path:
            detail += f"  ·  {popup[2]}"
        self.matrix_detail.set(detail)
        if point is not None:
            self.show_coordinates(point, popup)
        elif tip is not None or path:
            self.show_coordinates(tip if tip is not None else path[0], popup, show_pixel=False)
        else:
            self.hide_coordinates()

    def inspect_connection(self, _event=None):
        selected = self.connections.selection()
        if self.result and selected:
            self.show_connection(*self.result.connections[int(selected[0])])

    def inspect_equipment(self, _event=None):
        selected = self.equipment.selection()
        if not self.result or not selected:
            return
        label, equipment_type = self.result.equipment[int(selected[0])]
        self.show_equipment(label, equipment_type)

    def show_equipment(self, label, equipment_type, point=None):
        record = self.component_marks.get(label, {})
        box, tag_box = record.get("bbox"), record.get("tag_bbox")
        lines = [f"{label}  ·  {equipment_type}"]
        if box is not None:
            lines.append(f"Symbol {box} px")
        if tag_box is not None:
            lines.append(f"Tag {tag_box} px")
        self.matrix_detail.set("  ·  ".join(lines))
        if point is not None:
            self.show_coordinates(point, lines)
        elif box is not None or tag_box is not None:
            x0, y0, x1, y1 = box if box is not None else tag_box
            self.show_coordinates(((x0 + x1 - 1) / 2, (y0 + y1 - 1) / 2), lines, show_pixel=False)
        else:
            self.hide_coordinates()

    def save_matrix(self):
        if self.result:
            self.save_export(self.result.matrix_file, "connection matrix", "CSV table")

    def save_markings(self):
        if self.result and self.result.markings_file:
            self.save_export(self.result.markings_file, "markings JSON", "JSON file")

    def save_export(self, source, description, filetype):
        target = filedialog.asksaveasfilename(parent=self.root, title=f"Save {description}",
                                               defaultextension=source.suffix, initialfile=source.name,
                                               filetypes=[(filetype, f"*{source.suffix}")])
        if not target:
            return
        path = Path(target)
        try:
            if path.resolve() != source.resolve():
                shutil.copyfile(source, path)
        except OSError as exc:
            messagebox.showerror(f"Could not save {description}", str(exc), parent=self.root)
            return
        self.set_status(f"Saved {path.name}")

    def open_symbol_reference(self):
        try:
            os.startfile(str(SYMBOL_REFERENCE))
        except OSError as exc:
            messagebox.showerror("Could not open symbol reference", str(exc), parent=self.root)

    def open_results(self):
        if self.run_folder is None:
            return
        try:
            os.startfile(str(self.run_folder))
        except OSError as exc:
            messagebox.showerror("Could not open results", str(exc), parent=self.root)

    def close(self):
        self.executor.shutdown(wait=False)
        self.root.destroy()


def main():
    root = tk.Tk()
    DesktopApp(root)
    root.mainloop()
