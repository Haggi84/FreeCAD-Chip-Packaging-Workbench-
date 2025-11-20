from FreeCAD import Base
from PySide2 import QtCore
import FreeCAD, FreeCADGui ,Part


class ManualWireBonding:
    """Manual 2D wire bonding with controlled object selection."""
    
    def __init__(self):
        self.selected_objects = []  # Store [die_pad, bond_finger] pairs
        self.current_selection = None
        self.doc = FreeCAD.activeDocument()
        self.config = None
        self.is_active = False
        self.selection_mode = "waiting"  # "waiting", "select_die", "select_bond"
        
    def start_bonding_session(self, config):
        """Start manual wire bonding session."""
        self.config = config
        self.selected_objects = []
        self.is_active = True
        self.selection_mode = "select_die"
        self.doc = FreeCAD.activeDocument()
        
        if not self.doc:
            self.doc = FreeCAD.newDocument("WireBonding")
        
        FreeCAD.Console.PrintMessage("=== MANUAL 2D WIRE BONDING STARTED ===\n")
        FreeCAD.Console.PrintMessage("STEP 1: Click on a DIE PAD (starting point)\n")
        FreeCAD.Console.PrintMessage("STEP 2: Click on a BOND FINGER (ending point)\n") 
        FreeCAD.Console.PrintMessage("• Use 'Finish Wire Bonding' command to complete\n")
        
        # Set selection observer
        FreeCADGui.Selection.addObserver(self)
        
    def addSelection(self, doc, obj, sub, pos):
        """Handle selection events - but only for valid objects."""
        if not self.is_active or not self.config:
            return
            
        try:
            # Get the actual FreeCAD object
            freecad_obj = None
            if isinstance(obj, str):
                freecad_obj = self.doc.getObject(obj)
            else:
                freecad_obj = obj
                
            if not freecad_obj:
                FreeCAD.Console.PrintWarning("No valid object selected\n")
                return
                
            # Get object name for identification
            obj_name = freecad_obj.Name.lower()
            obj_label = freecad_obj.Label.lower() if hasattr(freecad_obj, 'Label') else ""
            
            FreeCAD.Console.PrintMessage(f"Selected: {freecad_obj.Name} | Mode: {self.selection_mode}\n")
            
            # STEP 1: Select DIE PAD
            if self.selection_mode == "select_die":
                # Check if this is a die pad (you can customize these conditions)
                if self.is_die_pad(freecad_obj, obj_name, obj_label):
                    # Get connection point from die pad
                    die_point = self.get_die_pad_connection_point(freecad_obj, sub)
                    
                    self.current_selection = {
                        'die_pad': freecad_obj,
                        'die_point': die_point,
                        'die_sub': sub
                    }
                    
                    FreeCAD.Console.PrintMessage(f"✓ DIE PAD selected: {freecad_obj.Name}\n")
                    FreeCAD.Console.PrintMessage(f"  Connection point: ({die_point.x:.3f}, {die_point.y:.3f})\n")
                    
                    # Create visual feedback
                    self.create_temp_marker(die_point, "die")
                    
                    # Move to next step
                    self.selection_mode = "select_bond"
                    FreeCAD.Console.PrintMessage("STEP 2: Now click on a BOND FINGER\n")
                    
                else:
                    FreeCAD.Console.PrintWarning("Please select a DIE PAD object\n")
                    FreeCAD.Console.PrintMessage("Look for objects named like: die_pad, pad, bond_pad, etc.\n")
            
            # STEP 2: Select BOND FINGER  
            elif self.selection_mode == "select_bond":
                # Check if this is a bond finger (you can customize these conditions)
                if self.is_bond_finger(freecad_obj, obj_name, obj_label):
                    # Get connection point from bond finger
                    bond_point = self.get_bond_finger_connection_point(freecad_obj, sub)
                    
                    # Create the bond wire
                    die_point = self.current_selection['die_point']
                    self.create_2d_bond_wire(die_point, bond_point)
                    
                    # Store the pair
                    self.selected_objects.append({
                        'die_pad': self.current_selection['die_pad'],
                        'bond_finger': freecad_obj,
                        'die_point': die_point,
                        'bond_point': bond_point
                    })
                    
                    FreeCAD.Console.PrintMessage(f"✓ BOND FINGER selected: {freecad_obj.Name}\n")
                    FreeCAD.Console.PrintMessage(f"✓ Bond created between {self.current_selection['die_pad'].Name} → {freecad_obj.Name}\n")
                    
                    # Reset for next bond
                    self.current_selection = None
                    self.selection_mode = "select_die"
                    FreeCAD.Console.PrintMessage("STEP 1: Click on next DIE PAD\n")
                    
                else:
                    FreeCAD.Console.PrintWarning("Please select a BOND FINGER object\n")
                    FreeCAD.Console.PrintMessage("Look for objects named like: bond_finger, finger, lead, etc.\n")
                    
        except Exception as e:
            FreeCAD.Console.PrintError(f"Error in selection: {str(e)}\n")
            import traceback
            FreeCAD.Console.PrintError(traceback.format_exc())
    
    def is_die_pad(self, obj, obj_name, obj_label):
        """Check if object is a die pad (customize these conditions)."""
        # Add your specific conditions for identifying die pads
        die_keywords = ['die', 'pad', 'bond_pad', 'chip_pad', 'ic_pad', 'metal1', 'drawing']
        
        # Check name and label
        for keyword in die_keywords:
            if keyword in obj_name or keyword in obj_label:
                return True
                
        # Check object type or properties
        if hasattr(obj, 'TypeId'):
            if 'Pad' in obj.TypeId or 'Face' in obj.TypeId:
                return True
                
        # If no specific identification, ask user to confirm
        FreeCAD.Console.PrintMessage(f"Object '{obj.Name}' selected as die pad. Is this correct? (Y/N)\n")
        # For now, assume yes - you can add user confirmation later
        return True
    
    def is_bond_finger(self, obj, obj_name, obj_label):
        """Check if object is a bond finger (customize these conditions)."""
        # Add your specific conditions for identifying bond fingers
        finger_keywords = ['finger', 'bond', 'lead', 'terminal', 'pin', 'leadframe']
        
        # Check name and label
        for keyword in finger_keywords:
            if keyword in obj_name or keyword in obj_label:
                return True
                
        # Check object type or properties
        if hasattr(obj, 'TypeId'):
            if 'Lead' in obj.TypeId or 'Terminal' in obj.TypeId:
                return True
                
        # If no specific identification, ask user to confirm
        FreeCAD.Console.PrintMessage(f"Object '{obj.Name}' selected as bond finger. Is this correct? (Y/N)\n")
        # For now, assume yes - you can add user confirmation later
        return True
    
    def get_die_pad_connection_point(self, die_pad, sub):
        """Get the connection point on a die pad."""
        try:
            if sub and "Vertex" in sub:
                # Use selected vertex
                vertex_index = int(sub.replace("Vertex", "")) - 1
                if vertex_index < len(die_pad.Shape.Vertexes):
                    return die_pad.Shape.Vertexes[vertex_index].Point
            elif sub and "Face" in sub:
                # Use face center
                face_index = int(sub.replace("Face", "")) - 1
                if face_index < len(die_pad.Shape.Faces):
                    return die_pad.Shape.Faces[face_index].CenterOfMass
            elif sub and "Edge" in sub:
                # Use edge midpoint
                edge_index = int(sub.replace("Edge", "")) - 1
                if edge_index < len(die_pad.Shape.Edges):
                    curve = die_pad.Shape.Edges[edge_index].Curve
                    if hasattr(curve, 'value'):
                        return curve.value(0.5)
        except:
            pass
            
        # Default: use object center
        return die_pad.Shape.BoundBox.Center
    
    def get_bond_finger_connection_point(self, bond_finger, sub):
        """Get the connection point on a bond finger."""
        try:
            if sub and "Vertex" in sub:
                # Use selected vertex
                vertex_index = int(sub.replace("Vertex", "")) - 1
                if vertex_index < len(bond_finger.Shape.Vertexes):
                    return bond_finger.Shape.Vertexes[vertex_index].Point
            elif sub and "Face" in sub:
                # Use face center
                face_index = int(sub.replace("Face", "")) - 1
                if face_index < len(bond_finger.Shape.Faces):
                    return bond_finger.Shape.Faces[face_index].CenterOfMass
            elif sub and "Edge" in sub:
                # Use edge midpoint
                edge_index = int(sub.replace("Edge", "")) - 1
                if edge_index < len(bond_finger.Shape.Edges):
                    curve = bond_finger.Shape.Edges[edge_index].Curve
                    if hasattr(curve, 'value'):
                        return curve.value(0.5)
        except:
            pass
            
        # Default: use object center
        return bond_finger.Shape.BoundBox.Center
    
    def create_temp_marker(self, position, marker_type):
        """Create temporary visual marker."""
        if not self.doc:
            return
            
        try:
            marker = self.doc.addObject("Part::Sphere", f"TempMarker_{marker_type}")
            marker.Radius = 0.15  # Slightly larger for visibility
            marker.Placement.Base = position
            
            # Color coding
            if marker_type == "die":
                marker.ViewObject.ShapeColor = (0.0, 1.0, 0.0)  # Green for die
            else:
                marker.ViewObject.ShapeColor = (0.0, 0.0, 1.0)  # Blue for bond
                
            marker.ViewObject.Transparency = 30
            self.doc.recompute()
            
            # Auto-remove after 2 seconds
            QtCore.QTimer.singleShot(2000, lambda: self.remove_temp_marker(marker))
        except Exception as e:
            FreeCAD.Console.PrintWarning(f"Could not create temp marker: {e}\n")
    
    def remove_temp_marker(self, marker):
        """Remove temporary marker."""
        try:
            if marker and marker in self.doc.Objects:
                self.doc.removeObject(marker.Name)
                if self.doc:
                    self.doc.recompute()
        except Exception as e:
            FreeCAD.Console.PrintWarning(f"Could not remove temp marker: {e}\n")
    
    def create_2d_bond_wire(self, start_point, end_point):
        """Create a 2D bond wire between die pad and bond finger."""
        if not self.doc:
            return None
            
        try:
            # Convert to 2D (XY plane)
            start_2d = Base.Vector(start_point.x, start_point.y, 0)
            end_2d = Base.Vector(end_point.x, end_point.y, 0)
            
            # Create wire
            if self.config.get('wire_style', 'straight') == 'arc':
                # Create arc wire
                arc_height = self.config.get('arc_height', 2.0)
                mid_point = Base.Vector(
                    (start_2d.x + end_2d.x) / 2,
                    (start_2d.y + end_2d.y) / 2,
                    arc_height
                )
                arc = Part.Arc(start_2d, mid_point, end_2d)
                wire_shape = arc.toShape()
            else:
                # Straight line wire
                wire_shape = Part.makeLine(start_2d, end_2d)
            
            # Create wire object
            bond_count = len(self.selected_objects)
            wire_obj = self.doc.addObject("Part::Feature", f"BondWire_{bond_count+1:03d}")
            wire_obj.Shape = wire_shape
            
            # Styling
            wire_obj.ViewObject.LineWidth = 3
            wire_obj.ViewObject.LineColor = (0.90, 0.75, 0.20)  # Gold
            wire_obj.ViewObject.PointSize = 5
            
            # Add properties
            wire_obj.addProperty("App::PropertyVector", "StartPoint", "Wirebond", "Die pad connection")
            wire_obj.addProperty("App::PropertyVector", "EndPoint", "Wirebond", "Bond finger connection")
            wire_obj.addProperty("App::PropertyLength", "WireLength", "Wirebond", "Wire length")
            wire_obj.addProperty("App::PropertyString", "NetName", "Wirebond", "Net name")
            
            wire_obj.StartPoint = start_point
            wire_obj.EndPoint = end_point
            wire_obj.WireLength = wire_shape.Length
            wire_obj.NetName = f"Net_{bond_count+1:03d}"
            
            self.doc.recompute()
            FreeCAD.Console.PrintMessage(f"✓ Bond wire created: {wire_obj.Name}\n")
            return wire_obj
            
        except Exception as e:
            FreeCAD.Console.PrintError(f"Error creating bond wire: {str(e)}\n")
            return None
    
    def finish_session(self):
        """Finish bonding session."""
        if not self.is_active:
            return 0
            
        self.is_active = False
        try:
            FreeCADGui.Selection.removeObserver(self)
        except:
            pass
        
        FreeCAD.Console.PrintMessage(f"=== WIRE BONDING FINISHED ===\n")
        FreeCAD.Console.PrintMessage(f"• Total bonds created: {len(self.selected_objects)}\n")
        self.generate_report()
        return len(self.selected_objects)
    
    def generate_report(self):
        """Generate bonding report."""
        if not self.selected_objects:
            FreeCAD.Console.PrintMessage("• No bonds were created.\n")
            return
            
        report = "=== WIRE BONDING REPORT ===\n"
        total_length = 0
        
        for i, bond in enumerate(self.selected_objects):
            length = bond['die_point'].distanceTo(bond['bond_point'])
            total_length += length
            report += f"Bond {i+1}: {bond['die_pad'].Name} → {bond['bond_finger'].Name} | Length: {length:.3f} mm\n"
        
        report += f"\nSUMMARY:\n"
        report += f"Total bonds: {len(self.selected_objects)}\n"
        report += f"Total wire length: {total_length:.3f} mm\n"
        report += f"Average length: {total_length/len(self.selected_objects):.3f} mm\n"
        
        FreeCAD.Console.PrintMessage(report)
    
    def cancel_session(self):
        """Cancel bonding session."""
        self.is_active = False
        try:
            FreeCADGui.Selection.removeObserver(self)
        except:
            pass
        self.selected_objects = []
        self.current_selection = None
        self.selection_mode = "waiting"
        FreeCAD.Console.PrintMessage("✗ Wire bonding cancelled.\n")

# Global instance
manual_bonder = ManualWireBonding()