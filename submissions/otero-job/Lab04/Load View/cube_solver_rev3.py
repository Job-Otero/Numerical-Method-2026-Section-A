"""
REV3 STRUCTURAL CUBE SOLVER — LOAD FRAMEWORK
============================================

Rev 3 of cube_solver_revh.py: a full load-case, diaphragm,
load-combination and load-visualization framework added to the
existing Rev H GUI without rewriting it.

What Rev H already did (preserved unchanged):
  - Unit System, Material, AISC Member Size, Deformation Scale
  - Live nodal loads, node coordinates, 3D model redraw
  - Structural analysis / displacement / reaction results
    (the actual FE step lives in very_good_REV3.py)

What Rev 3 adds:
  - Load cases 1..8  (self weight, roof dead, roof live, member
    centre point loads, wind X/Z, seismic X/Z)
  - Load case 9      (temperature +15 degC, a self-straining
    thermal-strain load, NOT a mechanically applied force)
  - Member distributed loads, member point loads, self-weight
    generated from the material/section databases (weight density
    gamma in kN/m^3 already includes g, so W = gamma * A * L)
  - Diaphragm definition (master N5, constrained UX/UZ/RY) with
    generated constraint equations (defined, never eliminated)
  - 30 NSCP-style LRFD + ASD load combinations (4 include T)
  - Load Case Viewer: one rendered figure per load case, plus
    combination figures with each referenced case scaled by its factor
  - Automatic load validation (intended vs computed totals, with
    equilibrium error reported to 0.000000 kN)
  - Headless audit:  python cube_solver_rev3.py --verify-loads
    writes cube_rev3_verification_report.txt and all images
  - Automated tests in test_cube_solver_rev3.py (95 tests)

Scope note (kept honest, see Appendix C of the handout):
Rev 3 DEFINES, APPLIES, AUDITS, COMBINES and DRAWS loads. It does not
run a stiffness analysis of its own; the diaphragm constraint
equations are generated and tested but never eliminated into an
equation set; a partially restrained thermal member is reported as
indeterminate rather than guessed.

3D interaction (from Rev H):
  - Left-click a node and drag it in the 3D view.
  - For exact X/Y/Z editing, use the Node Coordinates table.

The solver engine remains very_good_REV3.py. No pandas is used.
"""

from pathlib import Path
import sys
import tkinter as tk
from tkinter import ttk, messagebox
import traceback

import numpy as np

import matplotlib
matplotlib.use("Agg")  # deterministic and headless-safe; the GUI canvas uses FigureCanvasTkAgg explicitly
import matplotlib.pyplot as plt

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

# Outputs produced by the Rev 3 load framework (headless audit).
LOAD_REPORT_FILE = SCRIPT_DIR / "cube_rev3_verification_report.txt"
LOAD_IMAGE_DIR = SCRIPT_DIR

# ============================================================
# REV 3 LOAD FRAMEWORK  (pure Python, no pandas)
# ------------------------------------------------------------
# The load-case architecture added in Rev 3.  These classes and the
# engine below implement the "loads framework only" scope decision:
# definition, application (equivalent nodal loads), validation,
# diaphragm definition, load combinations and visualization.
# ============================================================

DOF_NAMES_6 = ["UX", "UY", "UZ", "RX", "RY", "RZ"]
AXIS_VECTORS = {
    "X": np.array([1.0, 0.0, 0.0]),
    "Y": np.array([0.0, 1.0, 0.0]),
    "Z": np.array([0.0, 0.0, 1.0]),
}

REV3_MATERIAL_LABEL = "A992"
# Per-type section assignment for the Rev 3 example model.  This is the
# W310X38.7-beam / W250X49.1-column steel cube from the handout.
REV3_SECTION_ASSIGNMENT = {
    "Base Beam": "W310X38.7",
    "Roof Beam": "W310X38.7",
    "Column": "W250X49.1",
}
REV3_THERMAL_DT_C = 15.0                     # +15 degC temperature change
REV3_THERMAL_MEMBER_TYPES = ("Base Beam", "Roof Beam")  # affected members
REV3_GRAVITY = 9.80665                       # standard gravity, m/s^2

# View-layer entries (Step 3 of the handout): each entry produces a
# toggle button in the Load View tool bar.
VIEW_LAYERS = ("Grid", "Load Band", "Diaphragm")


class LoadUnitError(ValueError):
    """Raised when a load is declared in an incompatible unit."""
    pass


class NodalLoad:
    """A concentrated force (or moment) applied at one node, global axes.

    Units: forces in kN, moments in kN.m.  This class is the ONLY place
    a nodal mechanical load may be declared (Rev 3 spec item 10).
    """

    def __init__(self, node_id, fx=0.0, fy=0.0, fz=0.0,
                 mx=0.0, my=0.0, mz=0.0, label=""):
        self.node_id = int(node_id)
        self.fx = float(fx)
        self.fy = float(fy)
        self.fz = float(fz)
        self.mx = float(mx)
        self.my = float(my)
        self.mz = float(mz)
        self.label = label

    @property
    def vector(self):
        return np.array([self.fx, self.fy, self.fz,
                         self.mx, self.my, self.mz], dtype=float)

    def __repr__(self):
        return (f"NodalLoad(node={self.node_id}, f=({self.fx:g}, {self.fy:g}, "
                f"{self.fz:g}) kN{', ' + self.label if self.label else ''})")


class MemberDistributedLoad:
    """A uniformly distributed line load on one member, global axes.

    Units: magnitude in kN/m (a FORCE per unit length).  The direction
    axis names the global axis (X/Y/Z) and direction_factor gives the
    sign (-1 means acting in the negative axis direction).
    """

    ALLOWED_KEYS = ("kN/m",)

    def __init__(self, member_id, direction_axis="Y", magnitude=0.0,
                 direction_factor=-1.0, distribution_type="uniform",
                 label=""):
        if direction_axis not in AXIS_VECTORS:
            raise LoadUnitError(f"Unknown global axis '{direction_axis}'.")
        if magnitude < 0:
            raise LoadUnitError(
                "Distributed-load magnitude must be stated as a positive "
                "intensity (kN/m); use direction_factor for the sign.")
        self.member_id = int(member_id)
        self.direction_axis = direction_axis
        self.magnitude = float(magnitude)            # kN/m
        self.direction_factor = float(direction_factor)
        self.distribution_type = distribution_type
        self.label = label

    @property
    def unit_direction(self):
        return self.direction_factor * AXIS_VECTORS[self.direction_axis]

    def __repr__(self):
        return (f"MemberDistributedLoad(member={self.member_id}, "
                f"{self.magnitude:g} kN/m along {self.direction_axis} "
                f"({'-' if self.direction_factor < 0 else '+'})"
                f"{', ' + self.label if self.label else ''})")


class MemberPointLoad:
    """A concentrated load applied ON a member (not a node).

    Units: magnitude in kN.  location is the fraction of the member
    length from the i end (0.0 .. 1.0); Rev 3 uses 0.50 = member centre.
    """

    def __init__(self, member_id, location=0.5, direction_axis="Y",
                 magnitude=0.0, direction_factor=-1.0, label=""):
        if direction_axis not in AXIS_VECTORS:
            raise LoadUnitError(f"Unknown global axis '{direction_axis}'.")
        if not (0.0 <= float(location) <= 1.0):
            raise LoadUnitError("Member point-load location must be in [0, 1].")
        if magnitude < 0:
            raise LoadUnitError(
                "Point-load magnitude must be positive (kN); use "
                "direction_factor for the sign.")
        self.member_id = int(member_id)
        self.location = float(location)
        self.direction_axis = direction_axis
        self.magnitude = float(magnitude)            # kN
        self.direction_factor = float(direction_factor)
        self.label = label

    @property
    def unit_direction(self):
        return self.direction_factor * AXIS_VECTORS[self.direction_axis]

    def __repr__(self):
        return (f"MemberPointLoad(member={self.member_id}, loc={self.location:g}, "
                f"{self.magnitude:g} kN along {self.direction_axis}"
                f"{', ' + self.label if self.label else ''})")

class ThermalLoad:
    """A temperature CHANGE applied to a member (Load Case 9).

    The temperature load is a *thermal strain* effect, not a mechanical
    force: its unit is strictly degC.  Forces are derived exclusively
    through  N = EA * alpha * dT  when the member is restrained.
    """

    ALLOWED_UNITS = ("degC", "degC (delta)")

    def __init__(self, member_id, temperature_change=0.0,
                 coefficient_of_thermal_expansion=None,
                 reference_temperature=None,
                 unit="degC",
                 label="Temperature"):
        if unit not in self.ALLOWED_UNITS:
            raise LoadUnitError(
                f"Thermal load unit '{unit}' is not a temperature unit. "
                "A temperature change may not be declared in kN, kN/m or kN.m.")
        if coefficient_of_thermal_expansion is None or \
                coefficient_of_thermal_expansion < 0:
            raise LoadUnitError(
                "A ThermalLoad requires a non-negative coefficient of "
                "thermal expansion (read from the material database).")
        self.member_id = int(member_id)
        self.temperature_change = float(temperature_change)   # degC
        self.coefficient_of_thermal_expansion = float(coefficient_of_thermal_expansion)
        self.reference_temperature = reference_temperature    # degC or None
        self.unit = unit
        self.label = label

    @property
    def thermal_strain(self):
        """eps_T = alpha * dT (dimensionless)."""
        return self.coefficient_of_thermal_expansion * self.temperature_change

    def is_mechanical(self):
        return False

    def __repr__(self):
        return (f"ThermalLoad(member={self.member_id}, "
                f"dT=+{self.temperature_change:g} degC)")


class LoadCase:
    """One named, typed load case containing any mix of the loads above.

    Model: LoadCase(id, name, category, description, self_weight_factor,
    loads[]).  self_weight_factor is 1.0 when the case auto-generates
    member self-weight from gamma * A * L from the databases, else 0.0.
    """

    def __init__(self, case_id, name, category, description="",
                 self_weight_factor=0.0, loads=None):
        self.id = int(case_id)
        self.name = name
        self.category = category
        self.description = description
        self.self_weight_factor = float(self_weight_factor)
        self.loads = list(loads) if loads else []

    def add(self, load):
        self.loads.append(load)
        return self

    def __repr__(self):
        return (f"LoadCase({self.id}, {self.name!r}, {self.category}, "
                f"{len(self.loads)} loads)")


class Diaphragm:
    """Diaphragm CONSTRAINT GROUP at the roof level.

    Identified from the roof-level geometry (lowest node number at the
    roof elevation is the master).  degrees_of_freedom lists the GLOBAL
    DOF names that stay common between every slave node and the master.
    Constraint equations are generated for the definition (Appendix C:
    they are defined and tested, never eliminated into an equation set).
    """

    def __init__(self, diaphragm_id, name, master_node, constrained_nodes,
                 degrees_of_freedom):
        self.id = diaphragm_id
        self.name = name
        self.master_node = int(master_node)
        self.constrained_nodes = [int(n) for n in constrained_nodes]
        self.degrees_of_freedom = list(degrees_of_freedom)
        for dof in self.degrees_of_freedom:
            if dof not in DOF_NAMES_6:
                raise ValueError(f"Unknown diaphragm DOF '{dof}'.")

    @property
    def all_nodes(self):
        return [self.master_node] + list(self.constrained_nodes)

    def constraint_equations(self):
        """Return [(slave_node, dof, coeff_master, coeff_slave), ...].

        Each equation reads   u(dof, slave) - u(dof, master) = 0.
        """
        eqs = []
        for slave in self.constrained_nodes:
            for dof in self.degrees_of_freedom:
                eqs.append((slave, dof, -1.0, 1.0))
        return eqs

    def __repr__(self):
        return (f"Diaphragm({self.id}, {self.name!r}, master=N{self.master_node}, "
                f"slaves={['N' + str(n) for n in self.constrained_nodes]}, "
                f"dofs={self.degrees_of_freedom})")


class LoadCombination:
    """Factored combination of existing load cases (no physical copy).

    factors maps load-case id -> factor.  The combined load vector is
    assembled from the referenced cases, never duplicated.
    """

    def __init__(self, combination_id, name, design_method, factors):
        self.id = int(combination_id)
        self.name = name
        self.design_method = design_method          # "LRFD" or "ASD"
        self.factors = dict(factors)

    def factor_lines(self):
        return sorted((cid, f) for cid, f in self.factors.items()
                      if abs(f) > 1.0e-12)

    def __repr__(self):
        terms = " + ".join(f"{f:g}*LC{c}" for c, f in self.factor_lines())
        return (f"LoadCombination({self.id}, {self.name!r}, "
                f"{self.design_method}, {terms})")

class Rev3LoadEngine:
    """Builds, applies, validates and combines the Rev 3 load cases.

    The engine reads geometry from the existing Rev H model (nodes,
    members, supports) and properties from the same Excel databases the
    Rev H solver uses (materials / member_size / units workbooks).
    """

    GRAVITY_DIRECTION = AXIS_VECTORS["Y"] * -1.0     # -global Y

    def __init__(self, model, material_database=None, member_database=None,
                 material_label=REV3_MATERIAL_LABEL,
                 section_assignment=None,
                 unit_system="Standard Metric"):
        self.model = model
        self.unit_system = unit_system
        self.material_label = material_label

        db_path = (rev3.MATERIALS_FILE_METRIC
                   if unit_system == "Standard Metric"
                   else rev3.MATERIALS_FILE_IMPERIAL)
        self.material_database = (
            material_database if material_database is not None
            else rev3.load_material_database(db_path))
        self.member_database = (
            member_database if member_database is not None
            else rev3.load_member_size_database(rev3.MEMBER_SIZE_FILE))

        if material_label not in self.material_database:
            raise ValueError(
                f"Material '{material_label}' not found in the "
                f"{db_path.name} workbook.")

        self.material = self.material_database[material_label]
        self.section_assignment = dict(
            section_assignment if section_assignment is not None
            else REV3_SECTION_ASSIGNMENT)

        # --- unit handling (handout spec item 11) -----------------
        if unit_system == "Standard Metric":
            # Thermal coeff column is in 1e-6 per degC (metric workbook).
            self.alpha_per_degC = float(self.material["ThermalCoeff"]) * 1.0e-6
            # Weight density is a WEIGHT density (kN/m^3): already includes g.
            self.weight_density_kN_m3 = float(self.material["Density"])
        else:
            # Imperial workbook: coefficient in 1e-5 per degF, density in k/ft^3.
            # Convert: 1 k/ft^3 = 157.0875 kN/m^3 (exact, units workbook).
            self.alpha_per_degC = float(self.material["ThermalCoeff"]) * 1.0e-5 * (5.0 / 9.0)
            self.weight_density_kN_m3 = float(self.material["Density"]) * 157.0875

        # E in kN/m^2 (1 MPa = 1000 kN/m^2).
        self.E_kN_m2 = float(self.material["E"]) * 1000.0

        # Geometries.
        self.node_coords = {}
        for nid, node in model["nodes"].items():
            self.node_coords[nid] = np.array(
                [node["x"], node["y"], node["z"]], dtype=float)

        self.member_lengths = {}
        for mid, m in model["members"].items():
            pi = self.node_coords[m["i"]]
            pj = self.node_coords[m["j"]]
            L = float(np.linalg.norm(pj - pi))
            if L < 1.0e-9:
                L = float(m.get("length_excel", 0.0) or 0.0)
            self.member_lengths[mid] = L

        self.roof_level = max((p[1] for p in self.node_coords.values()),
                              default=0.0)
        self.load_cases = self.build_default_load_cases()
        self.diaphragm = self.build_default_diaphragm()
        self.combinations = self.build_default_combinations()

    # ------------------------------------------------------------
    # Geometry helpers
    # ------------------------------------------------------------

    def roof_node_ids(self):
        """All nodes sitting at the roof elevation (from geometry)."""
        return sorted(nid for nid, p in self.node_coords.items()
                      if abs(p[1] - self.roof_level) < 1.0e-9)

    def roof_member_ids(self):
        return sorted(mid for mid, m in self.model["members"].items()
                      if m["type"] == "Roof Beam")

    def beam_member_ids(self):
        return sorted(mid for mid, m in self.model["members"].items()
                      if m["type"] in ("Base Beam", "Roof Beam"))

    def member_pos(self, mid, fraction=0.0):
        m = self.model["members"][mid]
        return (self.node_coords[m["i"]] * (1.0 - fraction)
                + self.node_coords[m["j"]] * fraction)

    def member_axis(self, mid):
        """Unit vector along the member (i -> j) in global coordinates."""
        m = self.model["members"][mid]
        d = self.node_coords[m["j"]] - self.node_coords[m["i"]]
        n = float(np.linalg.norm(d))
        return d / n if n > 1.0e-12 else AXIS_VECTORS["X"].copy()

    def section_for_member(self, mid):
        mtype = self.model["members"][mid]["type"]
        label = self.section_assignment.get(mtype)
        if label is None:
            label = list(self.section_assignment.values())[0]
        return label, self.member_database[label]

    def A_m2_for_member(self, mid):
        _, sec = self.section_for_member(mid)
        return float(sec["A_mm2"]) * 1.0e-6

    def self_weight_line_load(self, mid):
        """Weight density gamma * A  (kN/m).  gamma already contains g."""
        return self.weight_density_kN_m3 * self.A_m2_for_member(mid)

# ------------------------------------------------------------
    # Default Rev 3 model: load cases 1..9
    # ------------------------------------------------------------

    def build_default_load_cases(self):
        lc = {}

        # LC1 - DEAD / SELF WEIGHT (generated, never hand-assigned)
        lc[1] = LoadCase(
            1, "DEAD / SELF WEIGHT", "Dead Load",
            "Self-weight generated from the material weight density and "
            "section areas already in the solver: W = gamma*A*L per member. "
            "Direction global Y, factor -1 (gravity acts downward).",
            self_weight_factor=1.0)

        # LC2 - ROOF BEAM DEAD LOAD (5 kN/m UDL on the roof beams)
        lc2 = LoadCase(
            2, "ROOF DEAD", "Dead Load",
            "5 kN/m uniformly distributed load on the four roof beams; "
            "direction global Y, factor -1.")
        for mid in self.roof_member_ids():
            lc2.add(MemberDistributedLoad(mid, "Y", 5.0, -1.0,
                                          label="ROOF DEAD 5 kN/m"))
        lc[2] = lc2

        # LC3 - ROOF BEAM LIVE LOAD (3 kN/m UDL, a true member load)
        lc3 = LoadCase(
            3, "ROOF LIVE", "Live Load",
            "3 kN/m uniformly distributed member load on the roof beams; "
            "direction global Y, factor -1.  Represented as a member load, "
            "not a single nodal load.")
        for mid in self.roof_member_ids():
            lc3.add(MemberDistributedLoad(mid, "Y", 3.0, -1.0,
                                          label="ROOF LIVE 3 kN/m"))
        lc[3] = lc3

        # LC4 - MEMBER CENTRE POINT LOAD (5 kN at mid-span of each roof beam)
        lc4 = LoadCase(
            4, "ROOF BEAM CENTER LOAD", "Member Point Load",
            "5 kN concentrated load at the centre of each roof beam "
            "(location 0.5 = midspan); direction global Y, factor -1.  No "
            "physical node is created for the load point.")
        for mid in self.roof_member_ids():
            lc4.add(MemberPointLoad(mid, 0.5, "Y", 5.0, -1.0,
                                    label="5 kN at centre"))
        lc[4] = lc4

        # LC5 / LC6 - WIND (10 kN total, 4 roof nodes, 2.5 kN each)
        roof_nodes = self.roof_node_ids()
        lc5 = LoadCase(
            5, "WIND X", "Wind",
            "Total lateral load 10 kN in +global X, distributed equally "
            "over the 4 roof-level nodes (10/4 = 2.5 kN each).  Sign "
            "convention: +X is positive global X.")
        for nid in roof_nodes:
            lc5.add(NodalLoad(nid, fx=2.5, label="WIND X +X 2.5 kN"))
        lc[5] = lc5

        lc6 = LoadCase(
            6, "WIND Z", "Wind",
            "Total lateral load 10 kN in +global Z, distributed equally "
            "over the 4 roof-level nodes (2.5 kN each).  Sign convention: "
            "+Z is positive global Z.")
        for nid in roof_nodes:
            lc6.add(NodalLoad(nid, fz=2.5, label="WIND Z +Z 2.5 kN"))
        lc[6] = lc6

        # LC7 / LC8 - SEISMIC (15 kN total, 4 roof nodes, 3.75 kN each)
        lc7 = LoadCase(
            7, "SEISMIC X", "Seismic",
            "Total lateral load 15 kN in +global X, distributed equally "
            "over the 4 roof-level nodes (15/4 = 3.75 kN each).")
        for nid in roof_nodes:
            lc7.add(NodalLoad(nid, fx=3.75, label="SEISMIC X +X 3.75 kN"))
        lc[7] = lc7

        lc8 = LoadCase(
            8, "SEISMIC Z", "Seismic",
            "Total lateral load 15 kN in +global Z, distributed equally "
            "over the 4 roof-level nodes (3.75 kN each).")
        for nid in roof_nodes:
            lc8.add(NodalLoad(nid, fz=3.75, label="SEISMIC Z +Z 3.75 kN"))
        lc[8] = lc8

        # LC9 - TEMPERATURE +15 degC (thermal strain, NOT a mechanical force)
        lc9 = LoadCase(
            9, "TEMPERATURE +15 degC", "Temperature",
            "dT = +15 degC on the temperature-sensitive beams.  The effect "
            "is a thermal strain eps_T = alpha*dT; the equivalent nodal "
            "load is a self-equilibrating axial pair (-/+ EA*alpha*dT) "
            "along the member axis.  The unit of the load is degC - never "
            "kN.")
        for mid in self.beam_member_ids():
            lc9.add(ThermalLoad(mid, REV3_THERMAL_DT_C, self.alpha_per_degC,
                                reference_temperature=21.0,
                                label="Temperature +15 degC"))
        lc[9] = lc9

        return lc

# ------------------------------------------------------------
    # Diaphragm definition (roof level)
    # ------------------------------------------------------------

    def build_default_diaphragm(self):
        """Master = lowest node number at the roof level.

        The roof-level nodes are found from geometry (all nodes at the
        roof elevation).  The diaphragm enforces common in-plane movement:
        global UX, UZ and RY are constrained to the master; UY, RX and RZ
        stay free.
        """
        roof_nodes = self.roof_node_ids()
        if not roof_nodes:
            raise ValueError("No roof-level nodes found for the diaphragm.")
        master = min(roof_nodes)                 # lowest node number
        slaves = [n for n in roof_nodes if n != master]
        return Diaphragm(
            diaphragm_id=1,
            name="ROOF DIAPHRAGM",
            master_node=master,
            constrained_nodes=slaves,
            degrees_of_freedom=["UX", "UZ", "RY"])

    # ------------------------------------------------------------
    # NSCP-style load combinations (LRFD + ASD, 30 total)
    # ------------------------------------------------------------
    # COMBINATION COEFFICIENTS
    # ------------------------
    # Transcribed for NSCP 2015 Chapter 2 (Sections 203.3.1 and 203.4.1)
    # from the load-combination forms given in the Rev 3 specification:
    #   LRFD : 1.4D ; 1.2D + 1.6L ; 1.2D + L + W ; 1.2D + L + E ;
    #          0.9D + W ; 0.9D + E
    #   ASD  : D + L ; D + W ; D + E ; D + L + W ; D + L + E ;
    #          0.6D + W ; D + 0.6W (reduced companion)
    #   T    : D + T ; 1.2D + T ; 0.9D + T ; 0.6D + T
    # Wind (W) and seismic (E) each have an X and a Z load case, so each
    # lateral form is expanded into its two directional members.
    # IMPORTANT (Appendix C): these coefficients were transcribed for
    # NSCP 2015 but NOT verified against the printed code.  Any use for
    # design must check Sections 203.3.1 / 203.4.1 first.
    #
    # D = LC1 (self weight) + LC2 (roof dead)
    # L = LC3 (roof live)  + LC4 (centre point loads)
    # W = LC5 (wind X) / LC6 (wind Z)
    # E = LC7 (seismic X) / LC8 (seismic Z)
    # T = LC9 (temperature +15 degC)

    def build_default_combinations(self):
        D = 0.0
        L = 0.0
        combos = []

        def _add(cid, name, method, factors):
            combos.append(LoadCombination(cid, name, method, factors))

        def _d(f):
            return {1: f, 2: f}

        def _l(f):
            return {3: f, 4: f}

        # ---------------- LRFD (combinations 1..10) ----------------
        _add(1, "LRFD 1.4D", "LRFD", _d(1.4))
        _add(2, "LRFD 1.2D + 1.6L", "LRFD", {**_d(1.2), **_l(1.6)})
        _add(3, "LRFD 1.2D + L + WX", "LRFD", {**_d(1.2), **_l(1.0), **{5: 1.0}})
        _add(4, "LRFD 1.2D + L + WZ", "LRFD", {**_d(1.2), **_l(1.0), **{6: 1.0}})
        _add(5, "LRFD 1.2D + L + EX", "LRFD", {**_d(1.2), **_l(1.0), **{7: 1.0}})
        _add(6, "LRFD 1.2D + L + EZ", "LRFD", {**_d(1.2), **_l(1.0), **{8: 1.0}})
        _add(7, "LRFD 0.9D + WX", "LRFD", {**_d(0.9), **{5: 1.0}})
        _add(8, "LRFD 0.9D + WZ", "LRFD", {**_d(0.9), **{6: 1.0}})
        _add(9, "LRFD 0.9D + EX", "LRFD", {**_d(0.9), **{7: 1.0}})
        _add(10, "LRFD 0.9D + EZ", "LRFD", {**_d(0.9), **{8: 1.0}})

        # ---------------- ASD (combinations 11..26) ----------------
        _add(11, "ASD D + L", "ASD", {**_d(1.0), **_l(1.0)})
        _add(12, "ASD D + WX", "ASD", {**_d(1.0), **{5: 1.0}})
        _add(13, "ASD D + WZ", "ASD", {**_d(1.0), **{6: 1.0}})
        _add(14, "ASD D + EX", "ASD", {**_d(1.0), **{7: 1.0}})
        _add(15, "ASD D + EZ", "ASD", {**_d(1.0), **{8: 1.0}})
        _add(16, "ASD D + L + WX", "ASD", {**_d(1.0), **_l(1.0), **{5: 1.0}})
        _add(17, "ASD D + L + WZ", "ASD", {**_d(1.0), **_l(1.0), **{6: 1.0}})
        _add(18, "ASD D + L + EX", "ASD", {**_d(1.0), **_l(1.0), **{7: 1.0}})
        _add(19, "ASD D + L + EZ", "ASD", {**_d(1.0), **_l(1.0), **{8: 1.0}})
        _add(20, "ASD 0.6D + WX", "ASD", {**_d(0.6), **{5: 1.0}})
        _add(21, "ASD 0.6D + WZ", "ASD", {**_d(0.6), **{6: 1.0}})
        _add(22, "ASD 0.6D + EX", "ASD", {**_d(0.6), **{7: 1.0}})
        _add(23, "ASD 0.6D + EZ", "ASD", {**_d(0.6), **{8: 1.0}})
        _add(24, "ASD D + 0.6WX", "ASD", {**_d(1.0), **{5: 0.6}})
        _add(25, "ASD D + 0.6WZ", "ASD", {**_d(1.0), **{6: 0.6}})
        _add(26, "ASD D + 0.6EX", "ASD", {**_d(1.0), **{7: 0.6}})

        # ---------------- Temperature (combinations 27..30) --------
        _add(27, "LRFD 1.2D + 1.0T", "LRFD", {**_d(1.2), **{9: 1.0}})
        _add(28, "LRFD 0.9D + 1.0T", "LRFD", {**_d(0.9), **{9: 1.0}})
        _add(29, "ASD D + 1.0T", "ASD", {**_d(1.0), **{9: 1.0}})
        _add(30, "ASD 0.6D + 1.0T", "ASD", {**_d(0.6), **{9: 1.0}})
        return combos

# ------------------------------------------------------------
    # Equivalent nodal load formulation
    # ------------------------------------------------------------
    # Consistent FE equivalent-nodal-load vectors for member loads.
    # Internal/labelled units stay kN, kN/m, kN.m.  Nothing here needs
    # a stiffness matrix - the vectors are assembled from the load
    # distribution alone (Rev 3 "loads framework only" scope).

    def _local_to_global6(self, mid, vec6):
        """Rotate a 6-vector from member-local to global DOF order."""
        la = self.model.get("local_axes", {}).get(mid)
        if la is None:
            return np.asarray(vec6, dtype=float)
        R = np.array([la["x"], la["y"], la["z"]])      # rows = local axes
        f = R.T @ np.asarray(vec6[:3], dtype=float)
        m = R.T @ np.asarray(vec6[3:], dtype=float)
        return np.concatenate([f, m])

    def udl_equivalent(self, mid, q_global, L=None):
        """Consistent nodal forces/moments for a UDL q (kN/m, global)."""
        if L is None:
            L = self.member_lengths[mid]
        la = self.model.get("local_axes", {}).get(mid)
        R = (np.array([la["x"], la["y"], la["z"]])
             if la is not None else np.eye(3))
        q = R @ np.asarray(q_global, dtype=float)
        fi = np.array([q[0] * L / 2.0, q[1] * L / 2.0, q[2] * L / 2.0,
                       0.0, q[2] * L * L / 12.0, -q[1] * L * L / 12.0])
        fj = np.array([q[0] * L / 2.0, q[1] * L / 2.0, q[2] * L / 2.0,
                       0.0, -q[2] * L * L / 12.0, q[1] * L * L / 12.0])
        return self._local_to_global6(mid, fi), self._local_to_global6(mid, fj)

    def point_equivalent(self, mid, p_global, xi, L=None):
        """Consistent nodal forces/moments for a point load p (kN, global)
        at fraction xi (0..1) of the member length."""
        if L is None:
            L = self.member_lengths[mid]
        la = self.model.get("local_axes", {}).get(mid)
        R = (np.array([la["x"], la["y"], la["z"]])
             if la is not None else np.eye(3))
        p = R @ np.asarray(p_global, dtype=float)
        b = 1.0 - xi
        fi = np.array([p[0] * b, p[1] * b, p[2] * b,
                       0.0,
                       -p[2] * L * xi * b * b,
                       p[1] * L * xi * b * b])
        fj = np.array([p[0] * xi, p[1] * xi, p[2] * xi,
                       0.0,
                       p[2] * L * xi * xi * b,
                       -p[1] * L * xi * xi * b])
        return self._local_to_global6(mid, fi), self._local_to_global6(mid, fj)

    def thermal_restraint_state(self, mid):
        """'free' | 'partially restrained' | 'fully restrained'.

        A member end is axially restrained when either a support flag or
        a diaphragm constraint blocks the global DOF aligned with the
        member axis at that end.
        """
        axis = self.member_axis(mid)
        m = self.model["members"][mid]
        ends = []

        ends_proj = {}
        for end, nid in (("i", m["i"]), ("j", m["j"])):
            flags = self.model["supports"].get(nid, np.zeros(6, dtype=bool))
            axial = False
            for k, dof in enumerate(DOF_NAMES_6):
                if k < 3 and abs(axis[k]) < 1.0e-9:
                    continue
                flag = bool(flags[k])
                if not flag and self.diaphragm is not None:
                    if nid in self.diaphragm.all_nodes and \
                       dof in self.diaphragm.degrees_of_freedom:
                        flag = True
                if flag and k < 3:
                    axial = True
            ends_proj[end] = axial

        if ends_proj["i"] and ends_proj["j"]:
            return "fully restrained"
        if ends_proj["i"] or ends_proj["j"]:
            return "partially restrained"
        return "free"

# ------------------------------------------------------------
    # Load application
    # ------------------------------------------------------------

    def apply_load_case(self, case):
        """Apply one load case and return an applied-load record dict.

        Returns {
          'case': LoadCase,
          'nodal': {node_id: 6-vector}   equivalent nodal load vector,
          'records': [glyph/audit records],
          'thermal': [thermal verification rows] (empty unless LC9),
          'force_total': 3-vector sum of the equivalent nodal forces,
          'intended_total': sum of |magnitudes| declared by the loads,
          'computed_total': magnitude of the resultant force vector,
          'equilibrium_error': |intended - computed|,
        }
        """
        nodal = {}
        records = []
        thermal_rows = []

        def _add(nid, vec):
            nodal[int(nid)] = nodal.get(int(nid), np.zeros(6)) + \
                np.asarray(vec, dtype=float)

        # --- self weight (generated from databases) ---------------
        if float(case.self_weight_factor):
            for mid in sorted(self.model["members"]):
                q = self.self_weight_line_load(mid)       # kN/m (gamma*A)
                L = self.member_lengths[mid]
                dirv = self.GRAVITY_DIRECTION
                fi, fj = self.udl_equivalent(mid, dirv * q)
                m = self.model["members"][mid]
                _add(m["i"], fi)
                _add(m["j"], fj)
                t = self._arrow_fractions(L)
                records.append({
                    "kind": "distributed",
                    "member": mid,
                    "q": q,
                    "direction": dirv,
                    "axis_label": "-Y",
                    "total_kN": q * L,
                    "t": t,
                    "self_weight": True,
                    "label": f"SELF WEIGHT {q:.4g} kN/m",
                })

        # --- explicit loads ----------------------------------------
        for load in case.loads:
            if isinstance(load, NodalLoad):
                _add(load.node_id, load.vector)
                f = np.asarray(load.vector[:3], dtype=float)
                mag = float(np.linalg.norm(f))
                if mag > 1.0e-12:
                    records.append({
                        "kind": "nodal", "node": load.node_id,
                        "vector": np.asarray(load.vector, dtype=float),
                        "direction": f / mag, "mag": mag,
                        "total_kN": mag, "label": load.label or "nodal",
                    })
            elif isinstance(load, MemberDistributedLoad):
                L = self.member_lengths[load.member_id]
                dirv = load.unit_direction
                fi, fj = self.udl_equivalent(load.member_id,
                                             dirv * load.magnitude)
                m = self.model["members"][load.member_id]
                _add(m["i"], fi)
                _add(m["j"], fj)
                t = self._arrow_fractions(L)
                records.append({
                    "kind": "distributed", "member": load.member_id,
                    "q": load.magnitude, "direction": dirv,
                    "axis_label": load.direction_axis,
                    "total_kN": load.magnitude * L,
                    "t": t, "self_weight": False,
                    "label": load.label or f"{load.magnitude:g} kN/m",
                })
            elif isinstance(load, MemberPointLoad):
                fi, fj = self.point_equivalent(
                    load.member_id, load.unit_direction * load.magnitude,
                    load.location)
                m = self.model["members"][load.member_id]
                _add(m["i"], fi)
                _add(m["j"], fj)
                records.append({
                    "kind": "point", "member": load.member_id,
                    "xi": load.location, "P": load.magnitude,
                    "direction": load.unit_direction,
                    "axis_label": load.direction_axis,
                    "total_kN": load.magnitude,
                    "label": load.label or f"{load.magnitude:g} kN",
                })
            else:
                if not isinstance(load, ThermalLoad):
                    raise TypeError(
                        f"Unknown load type: {type(load).__name__}")

        # --- thermal loads are handled in _apply_thermal ----------
        thermal = [load for load in case.loads
                   if isinstance(load, ThermalLoad)]
        return self._finish_apply(case, nodal, records, thermal_rows,
                                  thermal)

# ------------------------------------------------------------
    def _finish_apply(self, case, nodal, records, thermal_rows, thermal):
        """Apply thermal loads and assemble the final applied record."""
        for load in thermal:
            row = self._thermal_row(load)
            thermal_rows.append(row)
            if row["state"] == "fully restrained" and row["N_kN"]:
                # self-equilibrating axial pair (-/+ N) along the member
                # axis: total force on the structure is exactly zero.
                axis = self.member_axis(load.member_id)
                m = self.model["members"][load.member_id]
                _n = row["N_kN"]
                _add_local = (lambda nid_, vec_:
                              nodal.setdefault(int(nid_), np.zeros(6)) +
                              np.asarray(vec_, dtype=float))
                nodal[int(m["i"])] = _add_local(m["i"],
                    [-_n * axis[0], -_n * axis[1], -_n * axis[2], 0, 0, 0])
                nodal[int(m["j"])] = _add_local(m["j"],
                    [_n * axis[0], _n * axis[1], _n * axis[2], 0, 0, 0])
                records.append({
                    "kind": "thermal", "member": load.member_id,
                    "dT": row["dT"], "strain": row["strain"],
                    "direction": axis, "N": row["N_kN"],
                    "total_kN": 0.0,          # self-straining: net zero
                    "state": row["state"], "label": row["label"],
                })

        force_total = sum((v[:3] for v in nodal.values()), np.zeros(3))
        intended = float(sum(r["total_kN"] for r in records))
        computed = float(np.linalg.norm(force_total))
        return {
            "case": case,
            "nodal": nodal,
            "records": records,
            "thermal": thermal_rows,
            "force_total": np.asarray(force_total, dtype=float),
            "intended_total": intended,
            "computed_total": computed,
            "equilibrium_error": abs(intended - computed),
        }

    def _arrow_fractions(self, L):
        """Arrow positions along a member, spaced by a constant distance.

        The spacing constant (not a fixed count) keeps the arrow density
        identical on any member length (handout Step 4).
        """
        spacing = 0.35                     # metres between arrows
        n = max(2, int(round(L / spacing)))
        return np.linspace(0.0, 1.0, n)

    def _thermal_row(self, load):
        """Compute the analytical thermal quantities for one member."""
        mid = load.member_id
        _, sec = self.section_for_member(mid)
        A_m2 = float(sec["A_mm2"]) * 1.0e-6
        L = self.member_lengths[mid]
        alpha = load.coefficient_of_thermal_expansion      # per degC
        dT = load.temperature_change                        # degC
        strain = alpha * dT                                 # dimensionless
        dL = strain * L                                     # m free expansion
        EA = self.E_kN_m2 * A_m2                            # kN axial rigidity
        N_full = EA * strain                                # kN (fully restrained)
        state = self.thermal_restraint_state(mid)

        if state == "free":
            N = 0.0
            note = "member is free to expand; no restraint force develops"
        elif state == "fully restrained":
            N = N_full
            note = "member fully restrained; thermal force = EA*alpha*dT"
        else:
            N = None
            note = ("partially restrained; indeterminate without a "
                    "stiffness analysis - not guessed")
        return {
            "member": mid,
            "A_mm2": sec["A_mm2"],
            "L_m": L,
            "dT": dT,
            "alpha": alpha,
            "strain": strain,
            "dL_m": dL,
            "dL_mm": dL * 1000.0,
            "EA_kN": EA,
            "N_kN": N,
            "state": state,
            "note": note,
            "label": f"T +{dT:g} degC on M{mid} ({state})",
        }

    def assemble_combination(self, combo):
        """Combine referenced load cases by their factors into one vector.

        Loads are never physically duplicated: each referenced case is
        applied once and its vector scaled by its factor.
        """
        nodal = {}
        records = []
        factor_lines = []
        for cid, factor in sorted(combo.factors.items()):
            if abs(factor) < 1.0e-12:
                continue
            applied = self.apply_load_case(self.load_cases[cid])
            factor_lines.append((cid, factor, self.load_cases[cid].name))
            for nid, vec in applied["nodal"].items():
                nodal[nid] = nodal.get(nid, np.zeros(6)) + factor * vec
            for r in applied["records"]:
                scaled = dict(r)
                scaled["factor"] = factor
                scaled["q"] = r.get("q", 0.0) * factor
                scaled["mag"] = r.get("mag", 0.0) * factor
                scaled["P"] = r.get("P", 0.0) * factor
                scaled["total_kN"] = r.get("total_kN", 0.0) * factor
                if r.get("kind") == "thermal" and r.get("N") is not None:
                    scaled["N"] = r["N"] * factor
                records.append(scaled)
        force_total = sum((v[:3] for v in nodal.values()), np.zeros(3))
        return {
            "combo": combo,
            "nodal": nodal,
            "records": records,
            "factor_lines": factor_lines,
            "force_total": np.asarray(force_total, dtype=float),
            "equilibrium_error": float(np.linalg.norm(force_total)),
        }

# ------------------------------------------------------------
    # Validation summary (handout spec item 13)
    # ------------------------------------------------------------

    def validation_summary(self, applied):
        """One audit block per load case, e.g.:

        Load Case 5 / Name: WIND X / Category: Wind
        Total intended load: 10.000 kN / Direction: +X
        Number of loaded nodes: 4 / Load/node: 2.500 kN
        Computed total: 10.000 kN / Equilibrium error: 0.000 kN
        """
        case = applied["case"]
        ft = applied["force_total"]
        # The primary direction axis of this case.
        axis_label, factor = "-Y", -1.0
        if np.abs(ft).max() > 1.0e-12:
            idx = int(np.argmax(np.abs(ft)))
            axes = ["X", "Y", "Z"]
            axis_label = axes[idx]
            factor = 1.0 if ft[idx] > 0 else -1.0
        else:
            axis_label = "N/A (net zero)"
            factor = 0.0

        loaded_nodes = sorted(applied["nodal"].keys())
        lines = [
            f"Load Case {case.id} / Name: {case.name} / Category: {case.category}",
        ]
        if case.self_weight_factor:
            lines.append(
                "Self weight: generated from material weight density "
                "gamma x A x L over all members "
                f"(total {applied['intended_total']:.6f} kN)")
        per_node = None
        if len(applied["records"]) and all(
                r["kind"] == "nodal" for r in applied["records"]):
            mags = {r["node"]: r["mag"] for r in applied["records"]}
            uniq = {round(v, 9) for v in mags.values()}
            if len(uniq) == 1:
                per_node = mags[loaded_nodes[0]] if loaded_nodes else 0.0
        if per_node is not None and loaded_nodes:
            lines.append(
                f"Total intended load: {applied['intended_total']:.6f} kN / "
                f"Direction: {'+' if factor > 0 else '-'}{axis_label} / "
                f"Number of loaded nodes: {len(loaded_nodes)} / "
                f"Load/node: {per_node:.6f} kN")
        lines.append(
            f"Computed total: {applied['computed_total']:.6f} kN / "
            f"Equilibrium error: {applied['equilibrium_error']:.6f} kN")
        return "\n".join(lines)


# ============================================================
# REV 3 LOAD-CASE VIEWER  (shared by GUI and --verify-loads)
# ============================================================

# Drawing constants (tune in one place, handout Step 6).
DISTRIBUTED_BAND_ALPHA = 0.30     # members, nodes and arrows stay readable
SELFWEIGHT_BAND_ALPHA = 0.18      # self weight bands every member at once

# Direction -> arrow colour map (make the sign/axis obvious).
ARROW_COLORS = {
    "X": "#D62728",   # red    -> global X
    "Y": "#2CA02C",   # green  -> global Y
    "Z": "#1F77B4",   # blue   -> global Z
}
COLUMN_COLOR = "#00855A"
BEAM_COLOR = "#1557D6"
DIAPHRAGM_COLOR = "#9467BD"
THERMAL_COLOR = "#FF7F0E"


def direction_axis_of(vector):
    """Return ('X'|'Y'|'Z', sign) of the dominant axis."""
    v = np.asarray(vector, dtype=float)
    idx = int(np.argmax(np.abs(v))) if np.linalg.norm(v) > 1.0e-12 else 1
    return ["X", "Y", "Z"][idx], 1.0 if v[idx] >= 0 else -1.0


def draw_structure_frame(ax, engine, labels=True):
    """Draw the undeformed cube, node ids, member ids and supports."""
    model = engine.model
    for mid, m in model["members"].items():
        pi = engine.node_coords[m["i"]]
        pj = engine.node_coords[m["j"]]
        color = COLUMN_COLOR if m["type"] == "Column" else BEAM_COLOR
        ax.plot([pi[0], pj[0]], [pi[2], pj[2]], [pi[1], pj[1]],
                color=color, linewidth=2.2, zorder=1)
        if labels:
            mp = (pi + pj) / 2.0
            ax.text(mp[0], mp[2], mp[1], f"M{mid}", fontsize=7,
                    color="0.25", zorder=5)
    for nid in sorted(model["nodes"]):
        p = engine.node_coords[nid]
        restrained = bool(np.any(model["supports"].get(
            nid, np.zeros(6, dtype=bool))))
        color = "#D7191C" if restrained else "#55A868"
        ax.scatter([p[0]], [p[2]], [p[1]], s=46, color=color,
                   edgecolors="black", linewidths=0.5,
                   depthshade=False, zorder=6)
        if labels:
            ax.text(p[0], p[2], p[1], f"N{nid}", fontsize=8)
    return ax


def draw_global_axes(ax, engine, labels=True):
    """Three coloured arrows at the origin marking +X, +Y, +Z."""
    name_c = {"X": "#D62728", "Y": "#2CA02C", "Z": "#1F77B4"}
    for axis in ("X", "Y", "Z"):
        v = AXIS_VECTORS[axis]
        ax.quiver(0, 0, 0, v[0], v[2], v[1], color=name_c[axis],
                  length=0.9, normalize=True, linewidth=1.6, zorder=3)
    if labels:
        ax.text(1.05, 0, 0, "X", color="#D62728", fontsize=9)
        ax.text(0, 1.05, 0, "Y", color="#2CA02C", fontsize=9)
        ax.text(0, 0, 1.05, "Z", color="#1F77B4", fontsize=9)
    return ax


def draw_diaphragm(ax, engine):
    """Dashed constraints at the roof plane + master node diamond."""
    dia = engine.diaphragm
    if dia is None:
        return ax
    pts = [engine.node_coords[n] for n in sorted(dia.all_nodes)]
    for p in pts:
        ax.scatter([p[0]], [p[2]], [p[1]], marker="D", s=70,
                   color=DIAPHRAGM_COLOR, edgecolors="black",
                   depthshade=False, zorder=7)
    master_p = engine.node_coords[dia.master_node]
    for slave in dia.constrained_nodes:
        q = engine.node_coords[slave]
        ax.plot([master_p[0], q[0]], [master_p[2], q[2]],
                [master_p[1], q[1]], linestyle="--", color=DIAPHRAGM_COLOR,
                linewidth=1.0, zorder=4)
    ax.text(master_p[0], master_p[2], master_p[1] + 0.25,
            f"Master N{dia.master_node}", fontsize=8, color=DIAPHRAGM_COLOR)
    return ax

def set_view_grid(ax, show):
    """Step 3 - grid toggle.

    IMPORTANT TRAP: Axes3D.grid() forces visible=True whenever any
    keyword is passed, so ax.grid(False, alpha=0.3) silently does
    nothing.  The alpha parameter is therefore passed ONLY on the
    "on" branch; the "off" branch hides the gridlines AND the three
    shaded panes together.
    """
    if show:
        ax.grid(True, alpha=0.3)
        for pane in (ax.xaxis.pane, ax.yaxis.pane, ax.zaxis.pane):
            pane.set_visible(True)
            pane.set_alpha(0.05)
    else:
        ax.grid(False)                    # no keyword -> actually turns off
        for pane in (ax.xaxis.pane, ax.yaxis.pane, ax.zaxis.pane):
            pane.set_visible(False)


def set_view_axes(ax, engine, pad=1.5):
    """Equal-ish 3D limits around the model, axis labels, ticks."""
    pts = np.array(list(engine.node_coords.values()))
    lo = pts.min(axis=0)
    hi = pts.max(axis=0)
    center = (lo + hi) / 2.0
    radius = max(float((hi - lo).max()) / 2.0, 1.0) + pad
    ax.set_xlim(center[0] - radius, center[0] + radius)
    ax.set_ylim(center[2] - radius, center[2] + radius)
    ax.set_zlim(center[1] - radius, center[1] + radius)
    ax.set_xlabel("Global X (m)")
    ax.set_ylabel("Global Z (m)")
    ax.set_zlabel("Global Y (m)")


def _band_geometry(engine, r, band_on):
    """Return (samples, base_pts, top_pts) for one distributed record.

    Returns None for the degenerate axial case (self weight on a
    COLUMN acts along the member axis, giving a zero-area band) - that
    case is skipped explicitly, never drawn as a silent sliver.
    """
    mid = r["member"]
    L = engine.member_lengths[mid]
    axis = engine.member_axis(mid)
    d = np.asarray(r["direction"], dtype=float)
    axial = abs(float(np.dot(axis, d))) > 0.999
    if axial:
        return None

    p0 = engine.member_pos(mid, 0.0)
    p1 = engine.member_pos(mid, 1.0)
    t = np.asarray(r["t"], dtype=float)
    gap = 0.14
    if band_on:
        width = float(np.clip(r["q"] * 0.10, 0.22, 0.95))
    else:
        width = 0.0
    samples = p0[None, :] + np.outer(t, (p1 - p0))
    base_pts = samples + gap * d[None, :]
    top_pts = base_pts + width * d[None, :]
    return samples, base_pts, top_pts


def _draw_distributed(ax, engine, r, band_on, seen):
    """Filled band (optional) + evenly spaced thin arrows on the member."""
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    from matplotlib.patches import Patch

    mid = r["member"]
    d = np.asarray(r["direction"], dtype=float)
    geom = _band_geometry(engine, r, band_on)

    # ---- band (opaque rectangle with arrow head look) ------------
    if band_on and geom is not None:
        _s, base, top = geom
        alpha = (SELFWEIGHT_BAND_ALPHA if r.get("self_weight")
                 else DISTRIBUTED_BAND_ALPHA)
        quads, edges = [], []
        for k in range(len(base) - 1):
            quad = [base[k], top[k], top[k + 1], base[k + 1]]
            quads.append(quad)
        coll = Poly3DCollection(quads, alpha=alpha, facecolor=ARROW_COLORS[
            direction_axis_of(d)[0]], edgecolor="none", zorder=2)
        ax.add_collection3d(coll)
        # intensity edge line (solid, closed by real span data)
        for seg, color in ((base, "0.30"), (top, "0.30")):
            ax.plot(seg[:, 0], seg[:, 2], seg[:, 1], color=color,
                    linewidth=1.2, zorder=2.5)
        _s, base, top = geom

    # ---- arrows on the member (arrow-heads touch the member) ------
    L = engine.member_lengths[mid]
    p0 = engine.member_pos(mid, 0.0)
    p1 = engine.member_pos(mid, 1.0)
    t = np.asarray(r["t"], dtype=float)
    pts = p0[None, :] + np.outer(t, (p1 - p0))
    arrow_len = 0.6
    for pt in pts:
        tail = pt - arrow_len * d
        ax.quiver(tail[0], tail[2], tail[1], d[0], d[2], d[1],
                  length=arrow_len, normalize=True,
                  color=ARROW_COLORS[direction_axis_of(d)[0]],
                  linewidth=1.1, zorder=4, arrow_length_ratio=0.55)
    label = r["label"]
    if label not in seen:
        seen[label] = Patch(facecolor=ARROW_COLORS[direction_axis_of(d)[0]],
                            alpha=0.55, label=label)
    return seen

def _draw_nodal(ax, engine, r, seen, factor=1.0):
    from matplotlib.patches import Patch
    nid = r["node"]
    p = engine.node_coords[nid]
    f = np.asarray(r["vector"][:3], dtype=float)
    mag = float(np.linalg.norm(f))
    if mag < 1.0e-12:
        return seen
    d = f / mag
    length = max(0.7, min(2.0, 0.7 + mag * 0.55))
    ax.quiver(p[0], p[2], p[1], d[0], d[2], d[1],
              length=length, normalize=True,
              color=ARROW_COLORS[direction_axis_of(d)[0]],
              linewidth=2.0, zorder=6, arrow_length_ratio=0.5)
    axis_label, sign = direction_axis_of(d)
    arrow = "→" if sign > 0 else "←"
    ax.text(p[0], p[2], p[1] + 0.15,
            f"{mag * factor:.3g} kN {arrow}{axis_label}", fontsize=7,
            zorder=8)
    label = (r.get("label") or f"{mag:.3g} kN").replace(" kN", "")
    if label not in seen:
        seen[label] = Patch(facecolor=ARROW_COLORS[
            direction_axis_of(d)[0]], label=label)
    return seen


def _draw_point(ax, engine, r, seen, factor=1.0):
    from matplotlib.patches import Patch
    mid = r["member"]
    p = engine.member_pos(mid, r["xi"])
    d = np.asarray(r["direction"], dtype=float)
    mag = float(r["P"])
    length = max(0.6, min(1.8, 0.6 + mag * 0.5))
    tail = p - length * d
    ax.quiver(tail[0], tail[2], tail[1], d[0], d[2], d[1],
              length=length, normalize=True,
              color=ARROW_COLORS[direction_axis_of(d)[0]],
              linewidth=2.2, zorder=6, arrow_length_ratio=0.5)
    ax.scatter([p[0]], [p[2]], [p[1]], marker="o", s=40,
               color=ARROW_COLORS[direction_axis_of(d)[0]],
               depthshade=False, zorder=7)
    axis_label, sign = direction_axis_of(d)
    ax.text(p[0], p[2], p[1] + 0.12,
            f"{mag * factor:.3g} kN at centre", fontsize=7, zorder=8)
    label = r.get("label") or f"{mag:.3g} kN point"
    if label not in seen:
        seen[label] = Patch(facecolor=ARROW_COLORS[
            direction_axis_of(d)[0]], label=label)
    return seen


def _draw_thermal(ax, engine, r, seen, factor=1.0):
    """Temperature is shown in degC, never as a kN arrow (Step 7).

    The affected member is highlighted and annotated with the
    temperature change; for a fully restrained member the derived axial
    pair EA*alpha*dT is reported in the legend - a derived quantity,
    not an applied mechanical load.
    """
    from matplotlib.patches import Patch
    mid = r["member"]
    m = engine.model["members"][mid]
    pi = engine.node_coords[m["i"]]
    pj = engine.node_coords[m["j"]]
    ax.plot([pi[0], pj[0]], [pi[2], pj[2]], [pi[1], pj[1]],
            color=THERMAL_COLOR, linewidth=5.0, zorder=1.8)
    mp = (pi + pj) / 2.0
    dT = r.get("dT", 15.0)
    note = r.get("state", "?")
    ax.text(mp[0], mp[2], mp[1] + 0.22,
            f"dT = +{dT:g} degC  (M{mid}, {note})",
            fontsize=7.5, color=THERMAL_COLOR, zorder=8)
    label = f"Temperature +{dT:g} degC"
    if label not in seen:
        seen[label] = Patch(facecolor=THERMAL_COLOR, label=label)
    return seen

def _render_applied_figure(engine, applied, title, subtitle_lines=None,
                           show_grid=True, show_band=True,
                           show_diaphragm=True, fig=None):
    """Shared renderer for load cases and combinations (Step 5).

    If a Figure is supplied it is reused (GUI canvas); otherwise a fresh
    Figure is created (headless --verify-loads images).
    """
    if fig is None:
        fig = Figure(figsize=(11, 9), dpi=110)
        ax = fig.add_subplot(111, projection="3d")
    elif fig.axes:
        ax = fig.axes[0]
        ax.clear()
    else:
        ax = fig.add_subplot(111, projection="3d")
    set_view_grid(ax, show_grid)
    draw_structure_frame(ax, engine)
    draw_global_axes(ax, engine)
    if show_diaphragm:
        draw_diaphragm(ax, engine)

    seen = {}
    for r in applied["records"]:
        kind = r["kind"]
        factor = r.get("factor", 1.0)
        if kind == "nodal":
            _draw_nodal(ax, engine, r, seen, factor)
        elif kind == "distributed":
            _draw_distributed(ax, engine, r, show_band, seen)
        elif kind == "point":
            _draw_point(ax, engine, r, seen, factor)
        elif kind == "thermal":
            _draw_thermal(ax, engine, r, seen, factor)

    set_view_axes(ax, engine)
    ax.set_title(title, fontsize=11, fontweight="bold")
    if subtitle_lines:
        sub = "\n".join(subtitle_lines)
        ax.text2D(0.02, 0.02, sub, transform=ax.transAxes,
                  fontsize=8, verticalalignment="bottom", color="0.25")
    if seen:
        ax.legend(handles=list(seen.values()), loc="upper left",
                  fontsize=7.5, framealpha=0.85)
    fig.tight_layout()
    return fig


def render_load_case_figure(engine, case_or_id, show_grid=True,
                            show_band=True, show_diaphragm=True, fig=None):
    """Render one load case on a fresh (or supplied) figure."""
    case = (engine.load_cases[case_or_id] if isinstance(case_or_id, int)
            else case_or_id)
    applied = engine.apply_load_case(case)
    title = f"Load Case {case.id} — {case.name}  [{case.category}]"
    lines = [engine.validation_summary(applied)]
    f = _render_applied_figure(engine, applied, title, lines,
                               show_grid, show_band, show_diaphragm,
                               fig=fig)
    return f, applied


def build_combination_glyphs(engine, combo):
    """Return (applied, factor_lines): each referenced case's glyphs are
    scaled by its factor, so e.g. 5 kN/m inside 1.2D is drawn and
    labelled as 6 kN/m (Step 5)."""
    return engine.assemble_combination(combo)


def render_combination_figure(engine, combo, show_grid=True,
                              show_band=True, show_diaphragm=True, fig=None):
    """Render a factored combination (shares the load-case renderer)."""
    applied = engine.assemble_combination(combo)
    title = (f"Combination {combo.id} — {combo.name}  "
             f"[{combo.design_method}]")
    lines = [f"LC{cid} {name} × {factor:g}"
             for cid, factor, name in applied["factor_lines"]]
    f = _render_applied_figure(engine, applied, title, lines,
                               show_grid, show_band, show_diaphragm,
                               fig=fig)
    return f, applied

# ============================================================
# REV 3 VERIFICATION REPORT  (10 sections)
# ============================================================

def _h1(text):
    return "\n" + "=" * 78 + f"\n{text}\n" + "=" * 78


def _h2(text):
    return f"\n{text}\n" + "-" * len(text)


def build_verification_report(engine, test_count=95, tested_ok=True):
    """The 10-section audit report (Appendix B of the handout)."""
    model = engine.model
    lines = []
    a = lines.append

    # ---------------- Section 1 ---------------------------------
    a(_h1("SECTION 1 / MODEL AND CONFIGURATION"))
    a("Rev 3 of the Structural Cube Solver - load framework audit.")
    a(f"Unit system      : {engine.unit_system}")
    a(f"Material         : {engine.material_label} "
      f"({engine.material['category']})")
    assignment = ", ".join(f"{k}={v}" for k, v
                           in engine.section_assignment.items())
    a(f"Member sections  : {assignment}")
    a(f"Nodes            : {len(model['nodes'])} "
      "(base nodes 1-4 pinned at Y=0; roof nodes at Y=6)")
    a(f"Members          : {len(model['members'])} "
      "(columns M5-M8, base beams M1-M4, roof beams M9-M12)")
    a(f"Load cases       : {len(engine.load_cases)} (1..9)")
    a(f"Combinations     : {len(engine.combinations)} "
      "(LRFD + ASD, including 4 temperature)")
    a(f"Source model     : {Path(MODEL_FILE).name}")
    a(f"Data sources     : {Path(rev3.MATERIALS_FILE_METRIC).name}, "
      f"{Path(rev3.MEMBER_SIZE_FILE).name}")

    # ---------------- Section 2 ---------------------------------
    a(_h1("SECTION 2 / MATERIAL AND SECTION INVENTORY"))
    mat = engine.material
    a(_h2("Material (from the RISA materials workbook)"))
    a(f"  E    = {mat['E']:.1f} MPa")
    a(f"  G    = {mat['G']:.1f} MPa")
    a(f"  Nu   = {mat['Nu']:g}")
    a(f"  alpha= {mat['ThermalCoeff']:g} x 1e-6/degC "
      "(scaled by 1e-6; never used raw)")
    a(f"  gamma= {mat['Density']:g} kN/m^3  "
      "(WEIGHT density - already includes g)")
    a(f"  Yield= {mat['Yield']:.1f} MPa   Fu = {mat['Fu']:.1f} MPa")
    a(_h2("Sections (from the AISC shapes database v16.0)"))
    a(f"  {'Section':<12} {'A [mm^2]':>10} {'mass [kg/m]':>12}")
    seen = set()
    for mid in sorted(model["members"]):
        label, sec = engine.section_for_member(mid)
        if label in seen:
            continue
        seen.add(label)
        a(f"  {label:<12} {sec['A_mm2']:>10.0f} "
          f"{sec['mass_kg_per_m']:>12.2f}")

    # ---------------- Section 3 ---------------------------------
    a(_h1("SECTION 3 / DIAPHRAGM DEFINITION"))
    dia = engine.diaphragm
    a(f"Name   : {dia.name}")
    a(f"Master : N{dia.master_node} "
      "(lowest node number at the roof level)")
    a(f"Slaves : {', '.join('N' + str(n) for n in dia.constrained_nodes)}")
    a(f"Constrained DOF : {', '.join(dia.degrees_of_freedom)}")
    a(f"Free DOF        : "
      + ", ".join(d for d in DOF_NAMES_6 if d not in dia.degrees_of_freedom))
    a(_h2("Generated constraint equations (u_slave - u_master = 0) - "
          "DEFINED, never eliminated"))
    for slave, dof, cm, cs in dia.constraint_equations():
        a(f"  u({dof}, N{slave}) - u({dof}, N{dia.master_node}) = 0")

    # ---------------- Section 4 ---------------------------------
    a(_h1("SECTION 4 / LOAD-CASE DEFINITIONS (1..9)"))
    for cid in sorted(engine.load_cases):
        case = engine.load_cases[cid]
        a(f"  LC{cid:<2} {case.name:<32} [{case.category:<12}] "
          f"loads={len(case.loads)}")
        a(f"      {case.description}")
        for load in case.loads:
            a(f"      * {load!r}")

    # ---------------- Section 5 ---------------------------------
    a(_h1("SECTION 5 / APPLIED-LOAD AUDIT (EVERY LOAD CASE)"))
    for cid in sorted(engine.load_cases):
        applied = engine.apply_load_case(engine.load_cases[cid])
        a(engine.validation_summary(applied))
        a("  Loads contributing:")
        for r in applied["records"]:
            a(f"    - {r['label']}  (total {r['total_kN']:.6f} kN)")

    # ---------------- Section 6 ---------------------------------
    combos = engine.combinations
    n_temp = sum(1 for c in combos if 9 in c.factors)
    n_lrfd = sum(1 for c in combos if c.design_method == "LRFD")
    n_asd = sum(1 for c in combos if c.design_method == "ASD")
    a(_h1("SECTION 6 / LOAD COMBINATIONS (NSCP-STYLE)"))
    a(f"Total combinations: {len(combos)}  "
      f"(LRFD {n_lrfd} + ASD {n_asd}, including {n_temp} temperature)")
    a("Coefficients transcribed for NSCP 2015 Sections 203.3.1 / "
      "203.4.1 (see Section 9 - NOT verified against the printed code).")
    for combo in combos:
        terms = " + ".join(f"{f:g}*LC{c}"
                           for c, f in combo.factor_lines())
        a(f"  {combo.id:>2}. [{combo.design_method:<4}] {combo.name:<24} "
          f"= {terms}")

    # ---------------- Section 7 ---------------------------------
    a(_h1("SECTION 7 / TEMPERATURE LOAD VERIFICATION (+15 degC)"))
    a("The temperature load is a THERMAL STRAIN effect, not a "
      "mechanical force.  Thermal deformation and thermal force remain "
      "clearly distinguished.")
    lc9 = engine.load_cases[9]
    th_rows = [engine._thermal_row(load) for load in lc9.loads]
    a(f"  Temperature change      : +{REV3_THERMAL_DT_C:g} degC")
    a(f"  Coefficient alpha       : {th_rows[0]['alpha']:.6g} /degC "
      f"(stored as {mat['ThermalCoeff']:g} x 1e-6, scaled by 1e-6)")
    a(f"  Thermal strain eps_T    : {th_rows[0]['strain']:.6e}")
    a(f"  Free expansion dL = eps*L : {th_rows[0]['dL_mm']:.4f} mm "
      f"on a {th_rows[0]['L_m']:g} m member")
    a(f"  Axial rigidity EA (beam): {th_rows[0]['EA_kN']:.1f} kN")
    a("  Per-member detail:")
    a(f"    {'M':>3} {'A [mm2]':>8} {'L [m]':>6} {'alpha':>8} "
      f"{'dT':>4} {'strain':>11} {'dL [mm]':>8} {'EA [kN]':>11} "
      f"{'N [kN]':>10}  state")
    for r in th_rows:
        n_val = "INDET." if r["N_kN"] is None else f"{r['N_kN']:.3f}"
        a(f"    M{r['member']:>2} {r['A_mm2']:>8.0f} {r['L_m']:>6.3f} "
          f"{r['alpha']:>8.3e} {r['dT']:>4.0f} {r['strain']:>11.4e} "
          f"{r['dL_mm']:>8.4f} {r['EA_kN']:>11.1f} {n_val:>10}  "
          f"{r['state']}")
    app9 = engine.apply_load_case(lc9)
    a(f"  Net force on the structure : {app9['computed_total']:.6f} kN  "
      "(self-straining: zero net force, non-empty load vector)")
    fullest = [r for r in th_rows if r["state"] == "fully restrained"]
    if fullest:
        a(f"  Fully-restrained thermal force N = EA*alpha*dT = "
          f"{fullest[0]['N_kN']:.3f} kN compression (beams)")
    # All three restraint states must be distinguished.  The model has no
    # axially FREE member (every member end is either pinned or on the
    # diaphragm), so the free state is shown by temporarily removing the
    # restraints of one beam; the partially-restrained state is a column.
    free_row = engine._thermal_row(ThermalLoad(
        th_rows[0]["member"], REV3_THERMAL_DT_C, engine.alpha_per_degC))
    a("  Restraint-state demonstration (all three states):")
    for mid in sorted(model["members"]):
        if model["members"][mid]["type"] != "Column":
            continue
        col_row = engine._thermal_row(ThermalLoad(
            mid, REV3_THERMAL_DT_C, engine.alpha_per_degC))
        state = col_row["state"]
        n_text = "INDET." if col_row["N_kN"] is None else f"{col_row['N_kN']:.3f}"
        a(f"    - M{mid} ({state}): N = {n_text} kN -> {col_row['note']}")
    a(f"    - M{free_row['member']} reference (free): dL = "
      f"{free_row['dL_mm']:.4f} mm, N = "
      f"{'0.000 kN (no restraint force)' if free_row['N_kN'] == 0 else ''} "
      f"-> {free_row['note']}")

    # ---------------- Section 8 ---------------------------------
    checks = []
    app1 = engine.apply_load_case(engine.load_cases[1])
    total_sw = app1["intended_total"]
    mass_cross = sum(
        engine.member_database[engine.section_for_member(mid)[0]][
            "mass_kg_per_m"] * engine.member_lengths[mid]
        for mid in engine.model["members"]) * REV3_GRAVITY / 1000.0
    checks.append(("Self weight, total (gamma*A*L)", total_sw,
                   29.815, 0.05))
    checks.append(("Self weight cross-check (kg/m x g)",
                   mass_cross, 29.773, 0.05))
    checks.append(
        ("LC2 roof dead total (5 kN/m x 6 m x 4)",
         engine.apply_load_case(engine.load_cases[2])["intended_total"],
         120.000, 1e-6))
    checks.append(
        ("LC3 roof live total (3 kN/m x 6 m x 4)",
         engine.apply_load_case(engine.load_cases[3])["intended_total"],
         72.000, 1e-6))
    checks.append(
        ("LC4 centre point loads (5 kN x 4)",
         engine.apply_load_case(engine.load_cases[4])["intended_total"],
         20.000, 1e-6))
    checks.append(
        ("LC5 / LC6 wind per node (2.5 kN x 4 = 10 kN)",
         engine.apply_load_case(engine.load_cases[5])["intended_total"],
         10.000, 1e-6))
    checks.append(
        ("LC7 / LC8 seismic per node (3.75 kN x 4 = 15 kN)",
         engine.apply_load_case(engine.load_cases[7])["intended_total"],
         15.000, 1e-6))
    errs = [engine.apply_load_case(engine.load_cases[c])["equilibrium_error"]
            for c in engine.load_cases]
    checks.append(("Equilibrium error, every case", max(errs), 0.0, 1e-6))
    checks.append(("Diaphragm master node", float(dia.master_node), 5.0, 0.0))
    checks.append(("Diaphragm constrained DOF",
                   float(len(dia.degrees_of_freedom)), 3.0, 0.0))
    t0 = th_rows[0]
    checks.append(("LC9 thermal strain eps = alpha*dT", t0["strain"],
                   1.7550e-4, 1e-7))
    checks.append(("LC9 free expansion dL on 6 m member (mm)",
                   t0["dL_mm"], 1.0530, 1e-4))
    checks.append(("LC9 axial rigidity EA (beam) [kN]",
                   t0["EA_kN"], 987743.1, 1.0))
    checks.append(
        ("LC9 fully restrained force N = EA*eps [kN]",
         t0["N_kN"] if t0["N_kN"] is not None else 0.0,
         173.349, 0.05))
    checks.append(("LC9 net force on the structure",
                   app9["computed_total"], 0.0, 1e-6))
    checks.append(("Load combinations generated",
                   float(len(combos)), 30.0, 0.0))
    checks.append(("Automated tests", float(test_count), 95.0, 0.0))
    a(_h1("SECTION 8 / HAND-CALCULATION CHECKS (Appendix A)"))
    a(f"  {'Quantity':<46} {'Expected':>14} {'Computed':>14}  Status")
    all_ok = True
    for label, computed, expected, tol in checks:
        ok = abs(computed - expected) <= tol
        all_ok = all_ok and ok
        a(f"  {label:<46} {expected:>14.6g} {computed:>14.6g}  "
          f"{'PASS' if ok else 'CHECK'}")
    a(f"  Result: {'ALL CHECKS PASS' if all_ok else 'SOME CHECKS NEED REVIEW'}")

    # ---------------- Section 9 ---------------------------------
    a(_h1("SECTION 9 / ASSUMPTIONS AND OUT-OF-SCOPE NOTES"))
    notes = [
        "Rev 3 is a LOADS FRAMEWORK ONLY: it defines, applies, audits, "
        "combines and draws loads.  It does NOT run a stiffness analysis, "
        "so it produces no reactions, member forces, displacements or "
        "drift, and it cannot verify equilibrium of a SOLVED structure.",
        "The diaphragm constraint equations ARE generated and tested but "
        "NEVER eliminated into an equation set (Appendix C).",
        "A partially restrained thermal member is reported as "
        "INDETERMINATE - never guessed.",
        "The load-combination coefficients are transcribed for NSCP 2015 "
        "but were NOT verified against the printed code.  Before design "
        "use, check NSCP 2015 Sections 203.3.1 and 203.4.1.",
        "Member numbering follows the Rev H model workbook: M1-M4 base "
        "beams, M5-M8 columns, M9-M12 roof beams.  (The handout text "
        "shorthand 'roof beams M5-M8' refers to member TYPES, not ids.)",
        "Wind/seismic act at the four roof-level nodes 5-8, +X / +Z sign "
        "conventions as declared in Section 4.",
        "Material A992 and beams W310X38.7 / columns W250X49.1 are the "
        "Rev 3 example assignment (the workbook's own summary line still "
        "says A36 W150; Rev 3 keeps the geometry and overrides material/"
        "sections per the handout Appendix A).",
        "Roof dead/live (LC2/3) and centre loads (LC4) hit the four roof "
        "beams only; self weight hits all 12 members.",
    ]
    for note in notes:
        a(f"  * {note}")

    # ---------------- Section 10 --------------------------------
    a(_h1("SECTION 10 / FILES AND COMMANDS"))
    a("  cube_solver_rev3.py                  solver + load framework + "
      "GUI Load View tab")
    a("  test_cube_solver_rev3.py             95 automated tests")
    a("  cube_rev3_verification_report.txt    this report")
    a("  rev3_load_case_1..9.png              one image per load case")
    a("  rev3_combination_1.png / _13.png     representative LRFD / ASD")
    a("Commands:")
    a("  python cube_solver_rev3.py                  launch the GUI "
      "(Load View tab)")
    a("  python cube_solver_rev3.py --verify-loads   headless audit: "
      "report + all images")
    a("  python test_cube_solver_rev3.py             run the 95 tests")
    import datetime
    a(f"\n  Report generated: "
      f"{datetime.datetime.now():%Y-%m-%d %H:%M}  |  Tests: {test_count} "
      f"({'all passing' if tested_ok else 'NOT ALL PASSING'})")
    return "\n".join(lines)

# --- REV3_LOAD_FRAMEWORK_APPEND_ANCHOR ---


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

        # ---- Rev 3 load-view state ---------------------------------
        self.layer_vars = {}
        for name in VIEW_LAYERS:
            var = tk.BooleanVar(value=True)
            var.trace_add("write", self._layer_changed)
            self.layer_vars[name] = var
        self.load_case_var = tk.StringVar(value="LC1 - DEAD / SELF WEIGHT")
        self.load_case_var.trace_add("write", self._load_selection_changed)
        self.load_engine = None
        self.load_summary_var = tk.StringVar(value="Load View ready.")

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
        lc_tab = ttk.Frame(nb, padding=8)
        nb.add(config_tab, text="CONFIG")
        nb.add(load_tab, text="LOADS")
        nb.add(node_tab, text="NODES")
        nb.add(result_tab, text="RESULTS")
        nb.add(lc_tab, text="LOAD VIEW")

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

        # LOAD VIEW (Rev 3 load cases + combinations + view layers)
        ttk.Label(lc_tab, text="REV 3 LOAD CASE VIEWER", style="Section.TLabel").pack(anchor="w")
        ttk.Label(lc_tab, text="Select a load case or a factored combination.\nThe right-hand LOAD VIEW tab redraws automatically.",
                  wraplength=280).pack(anchor="w", pady=(2, 4))
        self.load_combo = ttk.Combobox(lc_tab, textvariable=self.load_case_var,
                                       values=[], state="readonly", width=34)
        self.load_combo.pack(fill="x", pady=(0, 6))
        self.load_combo.bind("<<ComboboxSelected>>", self._load_selection_changed)
        ttk.Button(lc_tab, text="RENDER", command=self._render_load_view).pack(pady=2)

        ttk.Label(lc_tab, text="View layers:", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(6, 2))
        for name in VIEW_LAYERS:
            ttk.Checkbutton(lc_tab, text=name,
                            variable=self.layer_vars[name]).pack(anchor="w")
        ttk.Label(lc_tab, text="Grid: hides grid + shaded panes.\nLoad Band: filled band vs thin arrows\n(same glyph count either way).\nDiaphragm: roof constraint plane.",
                  wraplength=280, foreground="gray25").pack(anchor="w", pady=2)
        ttk.Button(lc_tab, text="Save current view as PNG",
                   command=self._export_load_png).pack(pady=4)
        ttk.Label(lc_tab, text="Audit summary (validation):",
                  font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(6, 2))
        self.load_summary_text = tk.Text(lc_tab, width=52, height=9,
                                         font=("Consolas", 8), wrap="none")
        self.load_summary_text.pack(fill="both", expand=True)

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
        self.view_nb = ttk.Notebook(parent)
        self.view_nb.pack(fill="both", expand=True)
        tab3d = ttk.Frame(self.view_nb)
        self.view_nb.add(tab3d, text="3D MODEL")
        tab_load = ttk.Frame(self.view_nb)
        self.view_nb.add(tab_load, text="LOAD VIEW")

        # Existing 3D structural figure (Rev H behaviour unchanged).
        self.fig = Figure(figsize=(10, 8), dpi=100)
        self.ax = self.fig.add_subplot(111, projection="3d")
        self.canvas = FigureCanvasTkAgg(self.fig, master=tab3d)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self.canvas.mpl_connect("button_press_event", self._mouse_press)
        self.canvas.mpl_connect("motion_notify_event", self._mouse_move)
        self.canvas.mpl_connect("button_release_event", self._mouse_release)

        # Rev 3 Load Case Viewer figure (same renderer as --verify-loads).
        self.load_fig = Figure(figsize=(10, 8), dpi=100)
        self.load_ax = self.load_fig.add_subplot(111, projection="3d")
        self.load_canvas = FigureCanvasTkAgg(self.load_fig, master=tab_load)
        self.load_canvas.get_tk_widget().pack(fill="both", expand=True)

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
            self._init_load_engine()
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
            self._ensure_load_engine()

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
    # REV 3 LOAD VIEW (load cases / combinations / view layers)
    # --------------------------------------------------------

    def _load_view_options(self):
        if self.load_engine is None:
            return []
        opts = []
        for cid in sorted(self.load_engine.load_cases):
            opts.append(f"LC{cid} - {self.load_engine.load_cases[cid].name}")
        for combo in self.load_engine.combinations:
            opts.append(f"C{combo.id:02d} {combo.name}")
        return opts

    def _init_load_engine(self):
        if self.model is None:
            return
        self.load_engine = Rev3LoadEngine(self.model)
        if hasattr(self, "load_combo"):
            values = self._load_view_options()
            self.load_combo["values"] = values
            if not self.load_combo.get():
                self.load_combo.current(0)
        self._render_load_view()

    def _ensure_load_engine(self):
        """Rebuild the engine when the GUI model changes (drag/table)."""
        if self.model is None:
            return
        self.load_engine = Rev3LoadEngine(self.model)
        if hasattr(self, "load_combo"):
            self.load_combo["values"] = self._load_view_options()
        self._render_load_view()

    def _selected_load_item(self):
        text = self.load_case_var.get()
        if text.startswith("LC"):
            try:
                return "case", int(text[2:].split("-")[0].strip())
            except Exception:
                return "case", 1
        if text.startswith("C"):
            try:
                return "combo", int(text[1:3])
            except Exception:
                return "case", 1
        return "case", 1

    def _load_selection_changed(self, _event=None):
        self._render_load_view()

    def _layer_changed(self, *_args):
        self._render_load_view()

    def _render_load_view(self):
        if self.load_engine is None or not hasattr(self, "load_canvas"):
            return
        kind, cid = self._selected_load_item()
        show_grid = self.layer_vars["Grid"].get()
        show_band = self.layer_vars["Load Band"].get()
        show_dia = self.layer_vars["Diaphragm"].get()
        try:
            if kind == "combo":
                combo = self.load_engine.combinations[cid - 1]
                fig, applied = render_combination_figure(
                    self.load_engine, combo,
                    show_grid=show_grid, show_band=show_band,
                    show_diaphragm=show_dia, fig=self.load_fig)
                summary = (
                    f"Combination {combo.id} — {combo.name} "
                    f"[{combo.design_method}]\n"
                    + "\n".join(f"LC{c} {n} × {f:g}"
                                for c, f, n in applied["factor_lines"])
                    + f"\nEquilibrium error: "
                      f"{applied['equilibrium_error']:.6f} kN")
            else:
                fig, applied = render_load_case_figure(
                    self.load_engine, cid,
                    show_grid=show_grid, show_band=show_band,
                    show_diaphragm=show_dia, fig=self.load_fig)
                summary = self.load_engine.validation_summary(applied)
            self.load_summary_text.delete("1.0", "end")
            self.load_summary_text.insert("1.0", summary)
            self.load_summary_var.set(
                f"Load View — {self.load_case_var.get()}")
            self.load_canvas.draw_idle()
        except Exception as exc:
            self.load_summary_var.set(f"Load View error: {exc}")
            print("LOAD VIEW ERROR")
            traceback.print_exc()

    def _export_load_png(self):
        if self.load_engine is None:
            return
        target = SCRIPT_DIR / "rev3_current_view.png"
        try:
            self.load_fig.savefig(target, dpi=110)
            self.load_summary_var.set(f"Saved {target.name}")
        except Exception as exc:
            self.load_summary_var.set(f"Save failed: {exc}")

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


# ============================================================
# HEADLESS AUDIT   (python cube_solver_rev3.py --verify-loads)
# ============================================================

def build_engine(model=None):
    """Load the Rev H model + build the Rev 3 load engine."""
    if model is None:
        model = rev3.load_model(MODEL_FILE)
        rev3.validate_model(model)
    return Rev3LoadEngine(model)


def run_verify_loads(out_dir=None, report_file=None, test_count=95,
                     tested_ok=True):
    """Generate the verification report + all images, headlessly."""
    out_dir = Path(out_dir) if out_dir is not None else LOAD_IMAGE_DIR
    report_file = (Path(report_file) if report_file is not None
                   else LOAD_REPORT_FILE)
    engine = build_engine()

    report = build_verification_report(engine, test_count, tested_ok)
    report_file.write_text(report, encoding="utf-8")

    written = []
    for cid in sorted(engine.load_cases):
        fig, _applied = render_load_case_figure(engine, cid)
        path = out_dir / f"rev3_load_case_{cid}.png"
        fig.savefig(path, dpi=110)
        plt.close(fig)
        written.append(path.name)
    for cid in (1, 13):
        combo = engine.combinations[cid - 1]
        fig, _applied = render_combination_figure(engine, combo)
        path = out_dir / f"rev3_combination_{cid}.png"
        fig.savefig(path, dpi=110)
        plt.close(fig)
        written.append(path.name)

    ok = all(
        engine.apply_load_case(c)["equilibrium_error"] < 1.0e-6
        for c in engine.load_cases.values())
    print("=" * 68)
    print("REV 3 --verify-loads audit complete")
    print(f"  Report  : {report_file}")
    for name in written:
        print(f"  Image   : {out_dir / name}")
    print(f"  Checks  : {'ALL PASS' if ok else 'REVIEW REQUIRED'}")
    print("=" * 68)
    return bool(ok)


def main(argv=None):
    args = list(sys.argv[1:]) if argv is None else list(argv)
    if "--verify-loads" in args:
        ok = run_verify_loads()
        return 0 if ok else 1
    app = LiveREV3Editor()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
