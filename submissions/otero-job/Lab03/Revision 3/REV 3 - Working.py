"""
REV3 LIVE 3D STRUCTURAL EDITOR
================================

A live GUI front-end for the validated very_good_REV3.py solver.

What is live:
  - Unit System
  - Material
  - AISC Member Size
  - Deformation Scale
  - Nodal loads
  - Node coordinates (X/Y/Z) from the GUI
  - 3D model redraw
  - Structural analysis / displacement / reaction results

3D interaction:
  - Left-click a node and drag it in the 3D view.
  - Dragging moves the selected node in the displayed X-Z plane while
    retaining its Y elevation. Release the mouse to solve again.
  - For exact X/Y/Z editing, use the Node Coordinates table.

The solver engine remains very_good_REV3.py. No pandas is used.
"""

from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox
import traceback

import numpy as np

try:
    import very_good_REV3 as rev3
except ImportError as exc:
    raise SystemExit(
        "Could not import very_good_REV3.py.\n"
        "Put this file in the same folder as very_good_REV3.py.\n\n"
        f"Original error: {exc}"
    )

from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg


SCRIPT_DIR = Path(__file__).resolve().parent
MODEL_FILE = SCRIPT_DIR / "cube_structural_model_Rev1.xlsx"
RESULT_FILE = SCRIPT_DIR / "cube_structural_analysis_results.xlsx"
PLOT_FILE = SCRIPT_DIR / "cube_structural_deformed_shape.png"


class LiveREV3Editor(tk.Tk):

    def __init__(self):
        super().__init__()

        self.title("REV3 — Live 3D Structural Editor")
        self.geometry("1600x950")
        self.minsize(1250, 760)

        self.model = None
        self.result = None
        self.drag_node = None
        self.drag_start = None
        self.drag_original = None
        self.update_job = None
        self.running = False

        self.unit_var = tk.StringVar(value=rev3.SELECTED_UNIT_SYSTEM)
        self.material_var = tk.StringVar(value=rev3.SELECTED_MATERIAL_LABEL)
        self.member_var = tk.StringVar(value=rev3.SELECTED_MEMBER_LABEL)
        self.scale_var = tk.StringVar(value=str(rev3.DEFORMATION_SCALE))
        self.status_var = tk.StringVar(value="Loading REV3 model...")

        self.load_vars = {}
        self.coord_vars = {}
        self.property_vars = {}

        self._load_databases()
        self._make_variables()
        self._build_ui()
        self._refresh_materials()
        self._update_properties()

        self.after(200, self.initial_run)

    # --------------------------------------------------------
    # DATA
    # --------------------------------------------------------

    def _load_databases(self):
        self.material_databases = {
            "Standard Metric": rev3.load_material_database(
                rev3.MATERIALS_FILE_METRIC
            ),
            "Imperial": rev3.load_material_database(
                rev3.MATERIALS_FILE_IMPERIAL
            ),
        }
        self.sections = rev3.load_member_size_database(
            rev3.MEMBER_SIZE_FILE
        )

    def _make_variables(self):
        for nid in range(1, 9):
            self.load_vars[nid] = {}
            for dof in ("FX", "FY", "FZ", "MX", "MY", "MZ"):
                default = "-10000" if dof == "FY" and nid >= 5 else "0"
                var = tk.StringVar(value=default)
                var.trace_add("write", self._schedule_live_update)
                self.load_vars[nid][dof] = var

            self.coord_vars[nid] = {
                "X": tk.StringVar(value="0"),
                "Y": tk.StringVar(value="0"),
                "Z": tk.StringVar(value="0"),
            }
            for var in self.coord_vars[nid].values():
                var.trace_add("write", self._schedule_coordinate_update)

    # --------------------------------------------------------
    # UI
    # --------------------------------------------------------

    def _build_ui(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Title.TLabel", font=("Segoe UI", 18, "bold"))
        style.configure("Section.TLabel", font=("Segoe UI", 11, "bold"))
        style.configure("Run.TButton", font=("Segoe UI", 10, "bold"), padding=8)

        header = ttk.Frame(self, padding=(12, 10))
        header.pack(fill="x")
        ttk.Label(header, text="REV3 STRUCTURAL SOLVER", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="  LIVE 3D EDITOR", font=("Segoe UI", 10, "bold")).pack(side="left", padx=10)
        ttk.Button(header, text="RUN NOW", style="Run.TButton", command=self.run_solver).pack(side="right")

        pane = ttk.Panedwindow(self, orient="horizontal")
        pane.pack(fill="both", expand=True, padx=10, pady=(0, 6))

        controls = ttk.Frame(pane, padding=8)
        viewport = ttk.Frame(pane, padding=4)
        pane.add(controls, weight=0)
        pane.add(viewport, weight=1)

        self._build_controls(controls)
        self._build_viewport(viewport)

        status = ttk.Frame(self, relief="sunken", padding=4)
        status.pack(fill="x")
        ttk.Label(status, textvariable=self.status_var, anchor="w").pack(side="left", fill="x", expand=True)
        ttk.Label(status, text="Live solver • No pandas", font=("Segoe UI", 8)).pack(side="right")

    def _build_controls(self, parent):
        nb = ttk.Notebook(parent)
        nb.pack(fill="both", expand=True)

        config_tab = ttk.Frame(nb, padding=10)
        load_tab = ttk.Frame(nb, padding=8)
        node_tab = ttk.Frame(nb, padding=8)
        result_tab = ttk.Frame(nb, padding=8)
        nb.add(config_tab, text="CONFIG")
        nb.add(load_tab, text="LOADS")
        nb.add(node_tab, text="NODES")
        nb.add(result_tab, text="RESULTS")

        # CONFIG
        ttk.Label(config_tab, text="MODEL CONFIGURATION", style="Section.TLabel").pack(anchor="w", pady=(0, 12))
        self._combo(config_tab, "Unit System", self.unit_var, list(rev3.UNIT_SYSTEM_CHOICES), self._config_changed)
        self._combo(config_tab, "Material", self.material_var, [], self._config_changed)
        self._material_combo = self._last_combo
        self._combo(config_tab, "AISC Member Size", self.member_var, sorted(self.sections), self._config_changed)
        self._member_combo = self._last_combo

        ttk.Label(config_tab, text="Deformation Scale", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(12, 2))
        e = ttk.Entry(config_tab, textvariable=self.scale_var)
        e.pack(fill="x")
        e.bind("<Return>", lambda _e: self.run_solver())

        ttk.Button(config_tab, text="APPLY / RUN REV3", style="Run.TButton", command=self.run_solver).pack(fill="x", pady=14)

        prop = ttk.LabelFrame(config_tab, text="Selected Excel Properties", padding=8)
        prop.pack(fill="x")
        for r, (label, key) in enumerate([
            ("Material", "material"), ("E", "E"), ("G", "G"),
            ("Yield", "Yield"), ("Fu", "Fu"), ("Section", "section"),
            ("A", "A"), ("d", "d"), ("bf", "bf"),
            ("Ix", "Ix"), ("Iy", "Iy"), ("J", "J"),
        ]):
            ttk.Label(prop, text=label, font=("Segoe UI", 8, "bold")).grid(row=r, column=0, sticky="w", padx=(0, 8), pady=2)
            self.property_vars[key] = tk.StringVar(value="—")
            ttk.Label(prop, textvariable=self.property_vars[key], font=("Consolas", 8)).grid(row=r, column=1, sticky="w", pady=2)

        # LOADS
        ttk.Label(load_tab, text="LIVE NODAL LOADS", style="Section.TLabel").pack(anchor="w")
        ttk.Label(load_tab, text="Editing these fields schedules a new REV3 analysis automatically.").pack(anchor="w", pady=(2, 8))
        lf = ttk.Frame(load_tab)
        lf.pack(fill="x")
        for c, h in enumerate(("Node", "FX", "FY", "FZ", "MX", "MY", "MZ")):
            ttk.Label(lf, text=h, font=("Segoe UI", 7, "bold")).grid(row=0, column=c, padx=2, pady=2)
        for r, nid in enumerate(range(1, 9), 1):
            ttk.Label(lf, text=str(nid), font=("Segoe UI", 8, "bold")).grid(row=r, column=0)
            for c, dof in enumerate(("FX", "FY", "FZ", "MX", "MY", "MZ"), 1):
                ttk.Entry(lf, textvariable=self.load_vars[nid][dof], width=10, font=("Consolas", 8)).grid(row=r, column=c, padx=2, pady=2)

        # NODES
        ttk.Label(node_tab, text="LIVE NODE COORDINATES", style="Section.TLabel").pack(anchor="w")
        ttk.Label(node_tab, text="Edit X/Y/Z for exact geometry. The 3D view redraws automatically.").pack(anchor="w", pady=(2, 8))
        nf = ttk.Frame(node_tab)
        nf.pack(fill="x")
        for c, h in enumerate(("Node", "X", "Y", "Z")):
            ttk.Label(nf, text=h, font=("Segoe UI", 8, "bold")).grid(row=0, column=c, padx=3, pady=3)
        for r, nid in enumerate(range(1, 9), 1):
            ttk.Label(nf, text=str(nid)).grid(row=r, column=0)
            for c, axis in enumerate(("X", "Y", "Z"), 1):
                ttk.Entry(nf, textvariable=self.coord_vars[nid][axis], width=13, font=("Consolas", 8)).grid(row=r, column=c, padx=3, pady=3)
        ttk.Label(node_tab, text="Mouse drag: select a node in the 3D view and move it in the horizontal X-Z plane.", wraplength=280).pack(anchor="w", pady=12)

        # RESULTS
        self.result_text = tk.Text(result_tab, width=48, height=30, font=("Consolas", 8), wrap="none")
        self.result_text.pack(fill="both", expand=True)

    def _combo(self, parent, label, variable, values, callback):
        ttk.Label(parent, text=label, font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(5, 2))
        combo = ttk.Combobox(parent, textvariable=variable, values=values, state="readonly", width=30)
        combo.pack(fill="x", pady=(0, 6))
        combo.bind("<<ComboboxSelected>>", lambda _e: callback())
        self._last_combo = combo

    def _build_viewport(self, parent):
        self.fig = Figure(figsize=(10, 8), dpi=100)
        self.ax = self.fig.add_subplot(111, projection="3d")
        self.canvas = FigureCanvasTkAgg(self.fig, master=parent)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self.canvas.mpl_connect("button_press_event", self._mouse_press)
        self.canvas.mpl_connect("motion_notify_event", self._mouse_move)
        self.canvas.mpl_connect("button_release_event", self._mouse_release)

    # --------------------------------------------------------
    # CONFIGURATION
    # --------------------------------------------------------

    def _refresh_materials(self):
        database = self.material_databases[self.unit_var.get()]

        # Material labels are expected to be text. Some Excel/database
        # inputs can produce numeric keys (for example 36 or 50).
        # Python 3 cannot sort a mixture of int and str keys, so only
        # string material labels are presented to the GUI.
        values = sorted(
            (key for key in database.keys() if isinstance(key, str)),
            key=str
        )

        if not values:
            raise ValueError(
                f"No valid string material labels found for "
                f"unit system '{self.unit_var.get()}'."
            )

        self._material_combo["values"] = values

        if self.material_var.get() not in values:
            self.material_var.set(
                "A36 Gr.36" if "A36 Gr.36" in values else values[0]
            )

    def _config_changed(self):
        self._refresh_materials()
        self._update_properties()
        self._schedule_live_update()

    def _update_properties(self):
        try:
            system = self.unit_var.get()
            mat_label = self.material_var.get()
            sec_label = self.member_var.get()
            mat = self.material_databases[system][mat_label]
            sec = self.sections[sec_label]
            props, cfg = rev3.build_default_properties(system, mat_label, sec_label)
            self.property_vars["material"].set(mat_label)
            unit = "MPa" if system == "Standard Metric" else "ksi"
            self.property_vars["E"].set(f"{mat['E']:.3f} {unit}")
            self.property_vars["G"].set(f"{mat['G']:.3f} {unit}")
            self.property_vars["Yield"].set(f"{mat['Yield']:.3f} {unit}")
            self.property_vars["Fu"].set(f"{mat['Fu']:.3f} {unit}")
            self.property_vars["section"].set(sec_label)
            self.property_vars["A"].set(f"{sec['A_mm2']:.2f} mm²")
            self.property_vars["d"].set(f"{sec['d_mm']:.2f} mm")
            self.property_vars["bf"].set(f"{sec['bf_mm']:.2f} mm")
            self.property_vars["Ix"].set(f"{sec['Ix_mm4']:.4e} mm⁴")
            self.property_vars["Iy"].set(f"{sec['Iy_mm4']:.4e} mm⁴")
            self.property_vars["J"].set(f"{sec['J_mm4']:.4e} mm⁴")
        except Exception:
            pass

    def _configure_rev3(self):
        system = self.unit_var.get()
        material = self.material_var.get()
        member = self.member_var.get()
        scale = float(self.scale_var.get())
        if scale <= 0:
            raise ValueError("Deformation Scale must be greater than zero.")

        props, config = rev3.build_default_properties(system, material, member)
        rev3.SELECTED_UNIT_SYSTEM = system
        rev3.SELECTED_MATERIAL_LABEL = material
        rev3.SELECTED_MEMBER_LABEL = member
        rev3.SELECTED_MEMBER_DISPLAY = config["member_display"]
        rev3.DEFORMATION_SCALE = scale
        rev3._PROPS = props
        rev3.CONFIG = config
        rev3.DEFAULT_PROPERTIES = {
            "Base Beam": dict(props),
            "Roof Beam": dict(props),
            "Column": dict(props),
        }
        return config

    # --------------------------------------------------------
    # LIVE SOLVER
    # --------------------------------------------------------

    def _read_loads(self):
        loads = {}
        for nid in range(1, 9):
            vals = []
            for dof in ("FX", "FY", "FZ", "MX", "MY", "MZ"):
                raw = self.load_vars[nid][dof].get().strip()
                vals.append(0.0 if raw == "" else float(raw))
            if any(abs(v) > 0 for v in vals):
                loads[nid] = vals
        return loads

    def _read_coordinates_into_model(self):
        if self.model is None:
            return
        for nid in self.model["nodes"]:
            x = float(self.coord_vars[nid]["X"].get())
            y = float(self.coord_vars[nid]["Y"].get())
            z = float(self.coord_vars[nid]["Z"].get())
            self.model["nodes"][nid]["x"] = x
            self.model["nodes"][nid]["y"] = y
            self.model["nodes"][nid]["z"] = z

    def _load_coordinate_vars_from_model(self):
        for nid, node in self.model["nodes"].items():
            for axis, key in (("X", "x"), ("Y", "y"), ("Z", "z")):
                self.coord_vars[nid][axis].trace_remove("write", self._dummy_trace_id) if hasattr(self, "_dummy_trace_id") else None
                self.coord_vars[nid][axis].set(f"{node[key]:.6g}")

    def _make_load_vector(self, model, loads):
        F = np.zeros(6 * len(model["nodes"]))
        for nid, values in loads.items():
            if nid not in model["dof_map"]:
                raise ValueError(f"Unknown load node {nid}.")
            F[model["dof_map"][nid]] += np.asarray(values, dtype=float)
        return F

    def initial_run(self):
        try:
            self.model = rev3.load_model(MODEL_FILE)
            rev3.validate_model(self.model)
            self._load_coordinate_vars_from_model()
            self.run_solver(reload_model=False)
        except Exception as exc:
            self._show_error(exc)

    def run_solver(self, reload_model=True):
        if self.running:
            return
        self.running = True
        try:
            self.status_var.set("REV3 recalculating...")
            self.update_idletasks()

            config = self._configure_rev3()

            if reload_model or self.model is None:
                self.model = rev3.load_model(MODEL_FILE)
                rev3.validate_model(self.model)
                self._load_coordinate_vars_from_model()
            else:
                self._read_coordinates_into_model()

            loads = self._read_loads()
            self.model["nodal_loads"] = loads

            K, element_data = rev3.assemble_global_stiffness(self.model)
            F = self._make_load_vector(self.model, loads)
            U, reactions, free, restrained = rev3.solve_structure(
                self.model, K, F
            )
            element_forces = rev3.member_end_forces(
                self.model, U, element_data
            )

            # Keep the original REV3 Excel report updated.
            rev3.write_results(
                RESULT_FILE,
                self.model,
                U,
                reactions,
                loads,
                element_forces,
                element_data,
            )

            self.result = {
                "K": K,
                "F": F,
                "U": U,
                "reactions": reactions,
                "loads": loads,
                "element_data": element_data,
                "element_forces": element_forces,
                "free": free,
                "restrained": restrained,
                "config": config,
            }

            self._draw_live_3d()
            self._show_results()

            max_u = float(np.max(np.abs(U))) if len(U) else 0.0
            self.status_var.set(
                f"LIVE — {config['unit_system']} | {config['material_label']} | "
                f"{config['member_label']} | Max displacement = {max_u:.6e} m"
            )

        except Exception as exc:
            self.status_var.set(f"REV3 input/update error: {type(exc).__name__}")
            print("\nREV3 LIVE EDITOR ERROR")
            traceback.print_exc()
            # During typing, incomplete numeric input is normal. Do not spam dialogs.
        finally:
            self.running = False

    # --------------------------------------------------------
    # LIVE 3D RENDERER
    # --------------------------------------------------------

    def _draw_live_3d(self):
        model = self.model
        result = self.result
        U = result["U"]

        self.ax.clear()

        for mid, m in model["members"].items():
            ni, nj = m["i"], m["j"]
            pi = np.array([model["nodes"][ni]["x"], model["nodes"][ni]["y"], model["nodes"][ni]["z"]])
            pj = np.array([model["nodes"][nj]["x"], model["nodes"][nj]["y"], model["nodes"][nj]["z"]])

            ui = U[model["dof_map"][ni]][:3]
            uj = U[model["dof_map"][nj]][:3]
            di = pi + rev3.DEFORMATION_SCALE * ui
            dj = pj + rev3.DEFORMATION_SCALE * uj

            is_column = abs(pi[0] - pj[0]) < 1e-9 and abs(pi[2] - pj[2]) < 1e-9
            color = "#00855A" if is_column else "#1557D6"

            # Original geometry
            self.ax.plot(
                [pi[0], pj[0]], [pi[2], pj[2]], [pi[1], pj[1]],
                linestyle="--", linewidth=1.1, color="0.60", alpha=0.55
            )
            # Deformed geometry
            self.ax.plot(
                [di[0], dj[0]], [di[2], dj[2]], [di[1], dj[1]],
                linewidth=3.2, color=color
            )

            mp = (pi + pj) / 2
            self.ax.text(mp[0], mp[2], mp[1], f"M{mid}", fontsize=7)

        for nid in sorted(model["nodes"]):
            p = np.array([
                model["nodes"][nid]["x"],
                model["nodes"][nid]["y"],
                model["nodes"][nid]["z"],
            ])
            u = U[model["dof_map"][nid]][:3]
            d = p + rev3.DEFORMATION_SCALE * u

            restrained = model["supports"].get(nid, np.zeros(6, dtype=bool))
            color = "#D7191C" if np.any(restrained) else "#F45B69"

            self.ax.scatter(
                [d[0]], [d[2]], [d[1]],
                s=75, color=color, edgecolors="black", linewidths=0.6,
                depthshade=False, zorder=10
            )
            self.ax.text(d[0], d[2], d[1], f" N{nid}", fontsize=8)

        # Applied force arrows
        for nid, load in result["loads"].items():
            p = np.array([
                model["nodes"][nid]["x"],
                model["nodes"][nid]["y"],
                model["nodes"][nid]["z"],
            ])
            f = np.asarray(load[:3], dtype=float)
            mag = np.linalg.norm(f)
            if mag < 1e-12:
                continue
            direction = f / mag
            length = max(0.45, min(1.25, mag / 10000.0))
            self.ax.quiver(
                p[0], p[2], p[1],
                direction[0], direction[2], direction[1],
                length=length, normalize=True, color="#E53935", linewidth=2
            )

        # Supports as 3D markers
        for nid, flags in model["supports"].items():
            if not np.any(flags):
                continue
            p = model["nodes"][nid]
            self.ax.scatter(
                [p["x"]], [p["z"]], [p["y"]],
                marker="^", s=140, color="#D7191C", edgecolors="black", depthshade=False
            )

        self.ax.set_xlabel("Global X (m)")
        self.ax.set_ylabel("Global Z (m)")
        self.ax.set_zlabel("Global Y (m)")

        cfg = result["config"]
        self.ax.set_title(
            "REV3 — LIVE 3D STRUCTURAL MODEL\n"
            f"Units: {cfg['unit_system']}   |   Material: {cfg['material_label']}   |   "
            f"Member: {cfg['member_display']}",
            fontsize=11, fontweight="bold"
        )

        self._equal_axes(model)
        self.fig.tight_layout()
        self.canvas.draw_idle()

    def _equal_axes(self, model):
        pts = np.array([
            [n["x"], n["y"], n["z"]]
            for n in model["nodes"].values()
        ], dtype=float)
        lo = pts.min(axis=0)
        hi = pts.max(axis=0)
        center = (lo + hi) / 2
        radius = max((hi - lo).max() / 2, 1.0)

        self.ax.set_xlim(center[0] - radius, center[0] + radius)
        self.ax.set_ylim(center[2] - radius, center[2] + radius)
        self.ax.set_zlim(center[1] - radius, center[1] + radius)

    # --------------------------------------------------------
    # MOUSE NODE EDITING
    # --------------------------------------------------------

    def _project_node(self, p):
        # REV3 plot uses X, Z, Y as Matplotlib x,y,z.
        x2, y2, _ = self.ax.proj3d.proj_transform(
            p["x"], p["z"], p["y"], self.ax.get_proj()
        )
        return self.ax.transData.transform((x2, y2))

    def _mouse_press(self, event):
        if event.inaxes != self.ax or event.button != 1 or self.model is None:
            return

        best = None
        best_dist = 25.0
        for nid, p in self.model["nodes"].items():
            try:
                q = self._project_node(p)
            except Exception:
                continue
            dist = float(np.hypot(q[0] - event.x, q[1] - event.y))
            if dist < best_dist:
                best_dist = dist
                best = nid

        if best is None:
            return

        self.drag_node = best
        self.drag_start = (event.x, event.y)
        self.drag_original = dict(self.model["nodes"][best])
        self.status_var.set(f"Dragging Node {best} — release to recalculate")

    def _mouse_move(self, event):
        if self.drag_node is None or event.inaxes != self.ax:
            return

        p = self.model["nodes"][self.drag_node]
        q0 = self._project_node(p)

        # Numerical screen-to-model Jacobian for the horizontal X-Z plane.
        base = np.array([p["x"], p["z"]], dtype=float)
        eps = 1e-4
        jac = np.zeros((2, 2))
        for j in range(2):
            test = dict(p)
            if j == 0:
                test["x"] += eps
            else:
                test["z"] += eps
            q = self._project_node(test)
            jac[:, j] = (np.asarray(q) - np.asarray(q0)) / eps

        try:
            delta_screen = np.array([event.x - self.drag_start[0], event.y - self.drag_start[1]])
            delta_xz, *_ = np.linalg.lstsq(jac, delta_screen, rcond=None)
            p["x"] = self.drag_original["x"] + float(delta_xz[0])
            p["z"] = self.drag_original["z"] + float(delta_xz[1])
            self._draw_live_3d()
        except Exception:
            pass

    def _mouse_release(self, event):
        if self.drag_node is None:
            return
        self.drag_node = None
        self._sync_coord_table()
        self.run_solver(reload_model=False)

    def _sync_coord_table(self):
        if self.model is None:
            return
        for nid, p in self.model["nodes"].items():
            self.coord_vars[nid]["X"].set(f"{p['x']:.6g}")
            self.coord_vars[nid]["Y"].set(f"{p['y']:.6g}")
            self.coord_vars[nid]["Z"].set(f"{p['z']:.6g}")

    # --------------------------------------------------------
    # LIVE CALLBACKS
    # --------------------------------------------------------

    def _schedule_live_update(self, *_args):
        if self.running or self.model is None:
            return
        if self.update_job is not None:
            try:
                self.after_cancel(self.update_job)
            except Exception:
                pass
        self.update_job = self.after(550, self._live_run)

    def _live_run(self):
        self.update_job = None
        self.run_solver(reload_model=False)

    def _schedule_coordinate_update(self, *_args):
        if self.running or self.model is None:
            return
        if self.update_job is not None:
            try:
                self.after_cancel(self.update_job)
            except Exception:
                pass
        self.update_job = self.after(700, self._coordinate_run)

    def _coordinate_run(self):
        self.update_job = None
        try:
            self._read_coordinates_into_model()
            self.run_solver(reload_model=False)
        except Exception as exc:
            self.status_var.set(f"Coordinate input error: {exc}")

    # --------------------------------------------------------
    # RESULTS
    # --------------------------------------------------------

    def _show_results(self):
        if self.result is None:
            return
        r = self.result
        U = r["U"]
        reactions = r["reactions"]
        summary = (
            "REV3 LIVE ANALYSIS\n"
            + "=" * 48 + "\n"
            f"Unit system : {r['config']['unit_system']}\n"
            f"Material    : {r['config']['material_label']}\n"
            f"Member      : {r['config']['member_label']}\n"
            f"Nodes       : {len(self.model['nodes'])}\n"
            f"Members     : {len(self.model['members'])}\n"
            f"Total DOF   : {len(U)}\n"
            f"Max |U|     : {np.max(np.abs(U)):.6e} m\n"
            f"Max |R|     : {np.max(np.abs(reactions)):.6e} N\n\n"
            "NODE DISPLACEMENTS\n"
            + "-" * 48 + "\n"
            f"{'Node':>4} {'UX':>10} {'UY':>10} {'UZ':>10}\n"
        )
        for nid in sorted(self.model["nodes"]):
            u = U[self.model["dof_map"][nid]][:3]
            summary += f"{nid:4d} {u[0]:10.3e} {u[1]:10.3e} {u[2]:10.3e}\n"

        self.result_text.delete("1.0", "end")
        self.result_text.insert("1.0", summary)

    # --------------------------------------------------------
    # ERROR
    # --------------------------------------------------------

    def _show_error(self, exc):
        print("\n" + "=" * 70)
        print("REV3 LIVE EDITOR ERROR")
        traceback.print_exc()
        print("=" * 70)
        messagebox.showerror(
            "REV3 Error",
            f"{type(exc).__name__}: {exc}\n\nSee the VS Code terminal for the traceback."
        )


if __name__ == "__main__":
    app = LiveREV3Editor()
    app.mainloop()
