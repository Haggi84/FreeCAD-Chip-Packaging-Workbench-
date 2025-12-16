SCHEMA = {"QFN": [("pins", int, 32),
                ("pitch_mm", float, 0.5),
                ("body_L_mm", float, 5.0),
                ("body_W_mm", float, 5.0), 
                ("body_H_mm", float, 0.9), 
                ("pad_L_mm", float, 0.3), 
                ("pad_W_mm", float, 0.2), 
                ("exposed_pad_L_mm", float, 3.0), 
                ("exposed_pad_W_mm", float, 3.0),
       ],
          "QFP":[
            ("pins", int, 64), 
            ("pitch_mm", float, 0.8), 
            ("body_L_mm", float, 14.0), 
            ("body_W_mm", float, 14.0), 
            ("body_H_mm", float, 1.2), 
            ("lead_len_mm", float, 1.5), 
            ("lead_thick_mm", float, 0.2),  
       ],
          "BGA": [ ("rows", int, 12), 
                  ("cols", int, 12), 
                  ("pitch_mm", float, 0.8), 
                  ("ball_diam_mm", float, 0.4), 
                  ("body_L_mm", float, 10.0), 
                  ("body_W_mm", float, 10.0), 
                  ("body_H_mm", float, 1.2), 
                  ("standoff_mm", float, 0.2),
                    ],
                    }


import os
import FreeCAD as App

from builders.qfn import build_qfn
from builders.qfp import build_qfp
from builders.bga  import build_bga

BUILDERS = {
    "QFN": build_qfn,
    "QFP": build_qfp,
    "BGA": build_bga,
}

def list_types():
    """Return list of package types for the dropdown."""
    return list(SCHEMA.keys())

def get_param_fields(pkg_type):
    """Return [(name, type, default), ...] for a given package type."""
    return SCHEMA[pkg_type]

def _validate(pkg_type, params):
    """Basic safety checks on input values."""
    if pkg_type in ("QFN", "QFP"):
        if params["pins"] % 4 != 0:
            raise ValueError("Pins must be a multiple of 4 for QFN/QFP")
    if pkg_type == "BGA":
        if params["rows"] < 1 or params["cols"] < 1:
            raise ValueError("Rows and columns must be >= 1")
    for k, v in params.items():
        if isinstance(v, (int, float)) and v <= 0:
            raise ValueError(f"{k} must be > 0")

def build_component(pkg_type, params, export_step=False):
    """Call the right builder and optionally export a STEP file."""
    _validate(pkg_type, params)

    if App.ActiveDocument is None:
        App.newDocument(f"{pkg_type}_pkg")

    export_path = None
    if export_step:
        mod_root = os.path.dirname(__file__)
        out_dir = os.path.join(mod_root, "resources", "models")
        if not os.path.isdir(out_dir):
            os.makedirs(out_dir)
        # simple auto name
        name_bits = [pkg_type, str(params.get("pins", ""))]
        step_name = "_".join([b for b in name_bits if b]) + ".step"
        export_path = os.path.join(out_dir, step_name)

    return BUILDERS[pkg_type](params, export_path)
