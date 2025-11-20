import FreeCAD
from FreeCAD import Base
import Part

def check_wirebond_prerequisites():
    """
    Check if wire bonding can be performed.
    Returns (can_bond, message) tuple.
    """
    doc = FreeCAD.activeDocument()
    
    if not doc:
        return False, "No active document. Please load a design first."
    
    # Check for die pads (bondable layers)
    has_die_pads = False
    has_leadframe = False
    
    for obj in doc.Objects:
        obj_name = obj.Name.lower()
        obj_label = obj.Label.lower() if hasattr(obj, 'Label') else ""
        
        # Check for die pads/bondable layers
        if any(keyword in obj_name or keyword in obj_label 
               for keyword in ['pad', 'metal', 'layer', 'bondable', 'die']):
            # Check if it has Bondable property
            if hasattr(obj, 'Bondable') and obj.Bondable:
                has_die_pads = True
            # Or check by name patterns
            elif any(keyword in obj_name for keyword in ['pad', 'metal1', 'topmetal']):
                has_die_pads = True
        
        # Check for leadframe
        if any(keyword in obj_name or keyword in obj_label 
               for keyword in ['leadframe', 'lead', 'finger', 'frame', 'qfn', 'qfp', 'bga']):
            has_leadframe = True
    
    # Determine if we can proceed
    if not has_die_pads and not has_leadframe:
        return False, "No die pads or leadframe found.\nPlease load GDS layers and create a leadframe first."
    elif not has_die_pads:
        return False, "No die pads found.\nPlease load GDS layers with bondable pads first."
    elif not has_leadframe:
        return False, "No leadframe found.\nPlease create a leadframe first using 'Leadframe Configurator'."
    
    return True, "Ready for wire bonding!"