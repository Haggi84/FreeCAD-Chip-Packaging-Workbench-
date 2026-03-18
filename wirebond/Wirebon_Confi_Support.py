import FreeCAD


def check_wirebond_prerequisites():
    """
    Check if wire bonding can be performed.
    Returns (can_bond, message) tuple.

    Detection strategy:
    - Die pads: objects with Bondable=True (set by LayeronLeadframe) or IsContactPoint=True
      (set by ContactPointTool).
    - Leadframe: objects whose Name matches the specific names created by build_leadframe()
      or build_housing() — LeadframeBody, LeadframeLeads, BGA_Balls, FinalHousing.
    """
    doc = FreeCAD.activeDocument()
    if not doc:
        return False, "No active document. Please load a design first."

    has_die_pads = False
    has_leadframe = False

    _LEADFRAME_NAMES = ("leadframebody", "leadframeleads", "bga_balls", "finalhousing", "lead_")

    for obj in doc.Objects:
        # Bondable GDS layer (set by LayeronLeadframe.configuration)
        if hasattr(obj, "Bondable") and obj.Bondable:
            has_die_pads = True
        # Explicit contact point marker (set by ContactPointTool)
        elif hasattr(obj, "IsContactPoint") and obj.IsContactPoint:
            has_die_pads = True

        # New individual lead fingers (build_leadframe sets IsLeadFinger)
        if hasattr(obj, "IsLeadFinger") and obj.IsLeadFinger:
            has_leadframe = True
        # Legacy compound leadframe objects
        elif any(obj.Name.lower().startswith(name) for name in _LEADFRAME_NAMES):
            has_leadframe = True

    if not has_die_pads and not has_leadframe:
        return False, "No die pads or leadframe found.\nPlease load GDS layers and create a leadframe first."
    if not has_die_pads:
        return False, "No die pads found.\nPlease load GDS layers with bondable pads first."
    if not has_leadframe:
        return False, "No leadframe found.\nPlease create a leadframe first using 'Leadframe Configurator'."

    return True, "Ready for wire bonding!"