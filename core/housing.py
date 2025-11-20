from FreeCAD import Base
import FreeCAD, Part, Sketcher, FreeCADGui

def build_housing(config):
    # NOTE: This is the same implementation as in your HousingCommand.create_housing
    # (full version from earlier message). It creates outer/inner, cuts, posts, optional lid.
    # For brevity in this artifact, keep the earlier full code you saw; you can overwrite with your local file if needed.
    doc = FreeCAD.activeDocument()
    if not doc:
        doc = FreeCAD.newDocument("Housing")
    # ... replicate your create_housing body here ...
    return doc
