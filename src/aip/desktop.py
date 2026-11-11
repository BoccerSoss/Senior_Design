"""Desktop view for running the existing local drawing analysis."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import shutil
import tkinter as tk
from tkinter import filedialog, font, messagebox, ttk

from PIL import Image, ImageTk

from .desktop_analysis import AnalysisError, analyze_drawing
from .io import ROOT, image_info

SAMPLE = ROOT / "data" / "input" / "four_component.png"
PAGE = "#edf2f5"
PANEL = "#ffffff"
INK = "#172c39"
MUTED = "#506574"
ACCENT = "#116a57"
LINE = "#d8e2e7"
STRIPE = "#f3f7f8"
ERROR = "#a12838"


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
        self.show_detections = tk.BooleanVar(root, False)
        self.filename = tk.StringVar(root)
        self.image_detail = tk.StringVar(root)
        self.status = tk.StringVar(root)
        self.matrix_detail = tk.StringVar(root)
        self.reference_detail = tk.StringVar(root)
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
        ttk.Label(header, text="P&ID Matrix Builder", style="Title.TLabel").pack(anchor="w")
        ttk.Label(header, text="Turn a piping drawing into a connection matrix.",
                  style="Subtitle.TLabel").pack(anchor="w", pady=(2, 0))

        toolbar = ttk.Frame(root, style="Panel.TFrame", padding=(13, 8))
        toolbar.grid(row=1, column=0, sticky="ew", padx=22, pady=(0, 12))
        toolbar.columnconfigure(0, weight=1)
        ttk.Label(toolbar, textvariable=self.filename, style="PanelHeading.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(toolbar, textvariable=self.image_detail, style="PanelMuted.TLabel").grid(row=1, column=0, sticky="w")
        self.sample_button = ttk.Button(toolbar, text="Use sample", command=lambda: self.load_drawing(SAMPLE))
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
        observations.columnconfigure((0, 1), weight=1, uniform="observations")
        observations.rowconfigure(1, weight=1)
        self.equipment_title = ttk.Label(observations, text="Equipment", style="PanelHeading.TLabel")
        self.equipment_title.grid(row=0, column=0, sticky="w", pady=(0, 5))
        self.connections_title = ttk.Label(observations, text="Direct connections", style="PanelHeading.TLabel")
        self.connections_title.grid(row=0, column=1, sticky="w", padx=(6, 0), pady=(0, 5))
        eq_frame, self.equipment = self.table(observations, height=4, horizontal=True)
        eq_frame.grid(row=1, column=0, sticky="nsew", padx=(0, 5))
        self.columns(self.equipment, ("Tag", "Type"), stretch=True)
        cn_frame, self.connections = self.table(observations, height=4, horizontal=True)
        cn_frame.grid(row=1, column=1, sticky="nsew", padx=(6, 0))
        self.columns(self.connections, ("Equipment A", "Equipment B"), stretch=True)
        self.reference_label = ttk.Label(results, textvariable=self.reference_detail,
                                         style="PanelMuted.TLabel", wraplength=370)
        self.reference_label.grid(row=5, column=0, sticky="w", pady=(8, 0))

        footer = ttk.Frame(root)
        footer.grid(row=3, column=0, sticky="ew", padx=22, pady=(9, 13))
        footer.columnconfigure(0, weight=1)
        self.status_label = ttk.Label(footer, textvariable=self.status, style="Status.TLabel", wraplength=635)
        self.status_label.grid(row=0, column=0, sticky="w")
        self.progress = ttk.Progressbar(footer, mode="indeterminate", length=85)
        self.progress.grid(row=0, column=1, padx=8)
        self.progress.grid_remove()
        self.folder_button = ttk.Button(footer, text="Open results folder", command=self.open_results)
        self.folder_button.grid(row=0, column=2, sticky="e")

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
        self.folder_button.configure(state="normal" if self.run_folder and not busy else "disabled")

    def clear_results(self):
        self.result = None
        self.run_folder = None
        self.overlay_image = None
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
        kind = "Synthetic example" if self.source_path == SAMPLE.resolve() else "Selected drawing"
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
            return
        scaled = source.copy()
        scaled.thumbnail((max(1, width - 20), max(1, height - 20)), Image.Resampling.LANCZOS)
        self.preview_photo = ImageTk.PhotoImage(scaled, master=self.root)
        self.canvas.delete("preview")
        self.canvas.create_image(width // 2, height // 2, image=self.preview_photo, tags="preview")

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
        kind = "Synthetic example" if self.source_path == SAMPLE.resolve() else "Selected drawing"
        self.image_detail.set(f"{kind}  ·  {self.source_image.width} × {self.source_image.height} pixels")
        self.show_detections.set(self.overlay_image is not None)
        self.columns(self.matrix, ("Tag", *result.matrix_labels), stretch=not result.matrix_labels,
                     rows=[(label,) for label in result.matrix_labels])
        self.fill_table(self.matrix, [(label, *values) for label, values in zip(result.matrix_labels, result.matrix)])
        self.columns(self.equipment, ("Tag", "Type"), rows=result.equipment)
        self.columns(self.connections, ("Equipment A", "Equipment B"), rows=result.connections)
        self.fill_table(self.equipment, result.equipment)
        self.fill_table(self.connections, result.connections)
        self.equipment_title.configure(text=f"Equipment ({len(result.equipment)})")
        self.connections_title.configure(text=f"Direct connections ({len(result.connections)})")
        if not result.equipment:
            self.matrix_detail.set("No equipment detected in this drawing.")
        elif not result.connections:
            self.matrix_detail.set("No direct connections detected.")
        else:
            self.matrix_detail.set("Select a matrix cell to inspect the connection.")
        if result.evaluation is not None:
            score = result.evaluation["connections"]["f1"]
            score_text = f"{score:.2f}" if score is not None else "unavailable"
            self.reference_detail.set(f"Synthetic reference comparison  ·  Connection F1 {score_text} (provisional)")
        self.set_status(f"Analysis complete  ·  {len(result.equipment)} equipment items  ·  {len(result.connections)} direct connections")
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
            self.matrix_detail.set(f"{labels[i]} is the same equipment in this row and column.")
        else:
            value = self.result.matrix[i][j]
            relation = "Direct connection" if value else "No direct connection"
            self.matrix_detail.set(f"{labels[i]} ↔ {labels[j]}  ·  {relation} ({value})")

    def save_matrix(self):
        if not self.result:
            return
        target = filedialog.asksaveasfilename(parent=self.root, title="Save connection matrix",
                                               defaultextension=".csv", initialfile="adjacency.csv",
                                               filetypes=[("CSV table", "*.csv")])
        if not target:
            return
        path = Path(target)
        try:
            if path.resolve() != self.result.matrix_file.resolve():
                shutil.copyfile(self.result.matrix_file, path)
        except OSError as exc:
            messagebox.showerror("Could not save matrix", str(exc), parent=self.root)
            return
        self.set_status(f"Matrix saved to {path}")

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
