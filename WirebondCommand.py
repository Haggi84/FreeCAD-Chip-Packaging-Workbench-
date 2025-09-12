from PySide2 import QtWidgets, QtCore, QtGui
import FreeCAD, Part, FreeCADGui, os, math
from FreeCAD import Base, Vector
import mymodule
from All_Class import WirebondConfigurator


def get_shape_center(shape_obj):
    """Get center point from any FreeCAD shape object, handling compounds."""
    try:
        shape = shape_obj.Shape
        
        # Handle compound shapes by getting bounding box center
        if hasattr(shape, 'BoundBox'):
            bb = shape.BoundBox
            center_x = (bb.XMin + bb.XMax) / 2
            center_y = (bb.YMin + bb.YMax) / 2
            center_z = (bb.ZMin + bb.ZMax) / 2
            return Vector(center_x, center_y, center_z)
        
        # Fallback to CenterOfMass if available
        elif hasattr(shape, 'CenterOfMass'):
            return shape.CenterOfMass
        
        # Final fallback - use first vertex if available
        elif hasattr(shape, 'Vertexes') and shape.Vertexes:
            return shape.Vertexes[0].Point
            
        else:
            FreeCAD.Console.PrintWarning(f"Could not determine center for shape {shape_obj.Label}\n")
            return Vector(0, 0, 0)
            
    except Exception as e:
        FreeCAD.Console.PrintError(f"Error getting shape center for {shape_obj.Label}: {str(e)}\n")
        return Vector(0, 0, 0)


class BondWireSelector(QtWidgets.QDialog):
    """Dialog for selecting die pads and bond finger pads for wire bonding."""
    
    def __init__(self, die_pads, bond_fingers, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Wire Bond Connection Setup")
        self.setMinimumSize(600, 400)
        
        self.die_pads = die_pads
        self.bond_fingers = bond_fingers
        self.connections = []  # List of (die_pad, bond_finger, net_name) tuples
        
        layout = QtWidgets.QVBoxLayout()
        
        # Instructions
        info_label = QtWidgets.QLabel(
            "Select die pads and bond finger pads to create wire bond connections.\n"
            "Each wire inherits the net from its source die pad."
        )
        layout.addWidget(info_label)
        
        # Connection setup area
        connection_group = QtWidgets.QGroupBox("Wire Bond Connections")
        connection_layout = QtWidgets.QVBoxLayout()
        
        # Add connection controls
        add_layout = QtWidgets.QHBoxLayout()
        
        self.die_pad_combo = QtWidgets.QComboBox()
        self.die_pad_combo.addItems([f"Die Pad: {pad.Label}" for pad in die_pads])
        add_layout.addWidget(QtWidgets.QLabel("Die Pad:"))
        add_layout.addWidget(self.die_pad_combo)
        
        self.bond_finger_combo = QtWidgets.QComboBox()
        self.bond_finger_combo.addItems([f"Bond Finger: {finger.Label}" for finger in bond_fingers])
        add_layout.addWidget(QtWidgets.QLabel("Bond Finger:"))
        add_layout.addWidget(self.bond_finger_combo)
        
        self.net_name_edit = QtWidgets.QLineEdit("Net_1")
        add_layout.addWidget(QtWidgets.QLabel("Net Name:"))
        add_layout.addWidget(self.net_name_edit)
        
        self.add_connection_btn = QtWidgets.QPushButton("Add Connection")
        self.add_connection_btn.clicked.connect(self.add_connection)
        add_layout.addWidget(self.add_connection_btn)
        
        connection_layout.addLayout(add_layout)
        
        # Connection list
        self.connection_list = QtWidgets.QListWidget()
        connection_layout.addWidget(self.connection_list)
        
        # Remove connection button
        self.remove_connection_btn = QtWidgets.QPushButton("Remove Selected")
        self.remove_connection_btn.clicked.connect(self.remove_connection)
        connection_layout.addWidget(self.remove_connection_btn)
        
        connection_group.setLayout(connection_layout)
        layout.addWidget(connection_group)
        
        # Auto-connection options
        auto_group = QtWidgets.QGroupBox("Auto-Connection Options")
        auto_layout = QtWidgets.QVBoxLayout()
        
        self.auto_connect_nearest_btn = QtWidgets.QPushButton("Auto-Connect by Nearest Distance")
        self.auto_connect_nearest_btn.clicked.connect(self.auto_connect_nearest)
        auto_layout.addWidget(self.auto_connect_nearest_btn)
        
        self.auto_connect_pattern_btn = QtWidgets.QPushButton("Auto-Connect by Pattern")
        self.auto_connect_pattern_btn.clicked.connect(self.auto_connect_pattern)
        auto_layout.addWidget(self.auto_connect_pattern_btn)
        
        auto_group.setLayout(auto_layout)
        layout.addWidget(auto_group)
        
        # OK/Cancel buttons
        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
        
        self.setLayout(layout)
    
    def add_connection(self):
        """Add a new wire bond connection."""
        die_pad_idx = self.die_pad_combo.currentIndex()
        bond_finger_idx = self.bond_finger_combo.currentIndex()
        net_name = self.net_name_edit.text().strip() or f"Net_{len(self.connections) + 1}"
        
        if die_pad_idx >= 0 and bond_finger_idx >= 0:
            die_pad = self.die_pads[die_pad_idx]
            bond_finger = self.bond_fingers[bond_finger_idx]
            
            connection = (die_pad, bond_finger, net_name)
            self.connections.append(connection)
            
            list_text = f"{die_pad.Label} → {bond_finger.Label} (Net: {net_name})"
            self.connection_list.addItem(list_text)
            
            # Auto-increment net name
            try:
                base_name = net_name.rsplit('_', 1)[0]
                num = int(net_name.rsplit('_', 1)[1]) + 1
                self.net_name_edit.setText(f"{base_name}_{num}")
            except (ValueError, IndexError):
                self.net_name_edit.setText(f"{net_name}_1")
    
    def remove_connection(self):
        """Remove selected connection."""
        current_row = self.connection_list.currentRow()
        if current_row >= 0:
            self.connections.pop(current_row)
            self.connection_list.takeItem(current_row)
    
    def auto_connect_nearest(self):
        """Auto-connect die pads to nearest bond fingers."""
        self.connections.clear()
        self.connection_list.clear()
        
        used_fingers = set()
        
        for i, die_pad in enumerate(self.die_pads):
            die_center = get_shape_center(die_pad)
            
            # Find nearest unused bond finger
            min_distance = float('inf')
            nearest_finger = None
            
            for finger in self.bond_fingers:
                if finger in used_fingers:
                    continue
                    
                finger_center = get_shape_center(finger)
                distance = die_center.distanceToPoint(finger_center)
                
                if distance < min_distance:
                    min_distance = distance
                    nearest_finger = finger
            
            if nearest_finger:
                used_fingers.add(nearest_finger)
                net_name = f"Net_{i + 1}"
                connection = (die_pad, nearest_finger, net_name)
                self.connections.append(connection)
                
                list_text = f"{die_pad.Label} → {nearest_finger.Label} (Net: {net_name})"
                self.connection_list.addItem(list_text)
    
    def auto_connect_pattern(self):
        """Auto-connect in order (first die pad to first finger, etc.)."""
        self.connections.clear()
        self.connection_list.clear()
        
        max_connections = min(len(self.die_pads), len(self.bond_fingers))
        
        for i in range(max_connections):
            die_pad = self.die_pads[i]
            bond_finger = self.bond_fingers[i]
            net_name = f"Net_{i + 1}"
            
            connection = (die_pad, bond_finger, net_name)
            self.connections.append(connection)
            
            list_text = f"{die_pad.Label} → {bond_finger.Label} (Net: {net_name})"
            self.connection_list.addItem(list_text)
    
    def get_connections(self):
        """Return the list of connections."""
        return self.connections


class WireBondDesignRuleChecker:
    """Design rule checker for wire bonding."""
    
    def __init__(self, config):
        self.config = config
        self.violations = []
    
    def check_all_rules(self, wires, bond_pads, bond_fingers):
        """Run all design rule checks."""
        self.violations.clear()
        
        self.check_wire_to_wire_spacing(wires)
        self.check_wire_lengths(wires)
        self.check_bond_finger_margins(wires, bond_fingers)
        self.check_electrical_rules(wires)
        
        return self.violations
    
    def check_wire_to_wire_spacing(self, wires):
        """Check minimum wire-to-wire spacing."""
        min_spacing = self.config["min_wire_spacing"]
        
        for i, wire1 in enumerate(wires):
            for j, wire2 in enumerate(wires[i+1:], i+1):
                try:
                    # Get the 3D curves of both wires
                    if hasattr(wire1.Shape, 'Edges') and wire1.Shape.Edges:
                        curve1 = wire1.Shape.Edges[0].Curve
                        curve2 = wire2.Shape.Edges[0].Curve
                        
                        # Sample points along each curve and find minimum distance
                        min_dist = float('inf')
                        for t1 in [x/10.0 for x in range(11)]:  # 11 sample points
                            pt1 = curve1.value(t1)
                            for t2 in [x/10.0 for x in range(11)]:
                                pt2 = curve2.value(t2)
                                dist = pt1.distanceToPoint(pt2)
                                min_dist = min(min_dist, dist)
                        
                        if min_dist < min_spacing:
                            self.violations.append({
                                'type': 'Wire-to-Wire Spacing',
                                'severity': 'Error',
                                'message': f"Wires {wire1.Label} and {wire2.Label} too close: {min_dist:.3f}mm < {min_spacing}mm",
                                'objects': [wire1, wire2]
                            })
                except Exception:
                    # Fallback to bounding box distance
                    try:
                        dist = wire1.Shape.distToShape(wire2.Shape)[0]
                        if dist < min_spacing:
                            self.violations.append({
                                'type': 'Wire-to-Wire Spacing',
                                'severity': 'Error',
                                'message': f"Wires {wire1.Label} and {wire2.Label} too close: {dist:.3f}mm < {min_spacing}mm",
                                'objects': [wire1, wire2]
                            })
                    except Exception as e:
                        FreeCAD.Console.PrintWarning(f"Could not check spacing between {wire1.Label} and {wire2.Label}: {str(e)}\n")
    
    def check_wire_lengths(self, wires):
        """Check wire length constraints."""
        min_length = self.config["min_wire_length"]
        max_length = self.config["max_wire_length"]
        
        for wire in wires:
            try:
                length = wire.Shape.Length
                
                if length < min_length:
                    self.violations.append({
                        'type': 'Wire Length',
                        'severity': 'Error',
                        'message': f"Wire {wire.Label} too short: {length:.3f}mm < {min_length}mm",
                        'objects': [wire]
                    })
                
                if length > max_length:
                    self.violations.append({
                        'type': 'Wire Length',
                        'severity': 'Error',
                        'message': f"Wire {wire.Label} too long: {length:.3f}mm > {max_length}mm",
                        'objects': [wire]
                    })
            except Exception as e:
                FreeCAD.Console.PrintWarning(f"Could not check length for wire {wire.Label}: {str(e)}\n")
    
    def check_bond_finger_margins(self, wires, bond_fingers):
        """Check bond finger margin requirements."""
        margin = self.config["bond_finger_margin"]
        
        for wire in wires:
            try:
                # Get wire end point
                if hasattr(wire.Shape, 'Edges') and wire.Shape.Edges:
                    end_point = wire.Shape.Edges[0].Vertexes[-1].Point
                    
                    # Find closest bond finger
                    for finger in bond_fingers:
                        if hasattr(finger.Shape, 'Faces') and finger.Shape.Faces:
                            face = finger.Shape.Faces[0]
                            dist_result = face.distToShape(Part.Vertex(end_point))
                            dist = dist_result[0]
                            
                            if dist < margin:
                                self.violations.append({
                                    'type': 'Bond Finger Margin',
                                    'severity': 'Warning',
                                    'message': f"Wire {wire.Label} too close to {finger.Label} edge: {dist:.3f}mm < {margin}mm",
                                    'objects': [wire, finger]
                                })
            except Exception as e:
                FreeCAD.Console.PrintWarning(f"Could not check margin for wire {wire.Label}: {str(e)}\n")
    
    def check_electrical_rules(self, wires):
        """Check electrical connectivity rules."""
        net_connections = {}
        
        # Group wires by net
        for wire in wires:
            net = getattr(wire, 'Net', 'Unknown')
            if net not in net_connections:
                net_connections[net] = []
            net_connections[net].append(wire)
        
        # Check for potential short circuits (same net, different terminals)
        for net, net_wires in net_connections.items():
            if len(net_wires) > 1:
                # This could indicate multiple connections to the same net
                # In some cases this is valid (fan-out), in others it's an error
                self.violations.append({
                    'type': 'Multiple Connections',
                    'severity': 'Info',
                    'message': f"Net {net} has {len(net_wires)} wire connections",
                    'objects': net_wires
                })


def find_bondable_objects():
    """Find objects that can be used for wire bonding."""
    doc = FreeCAD.activeDocument()
    if not doc:
        return [], []
    
    die_pads = []
    bond_fingers = []
    
    for obj in doc.Objects:
        if hasattr(obj, 'Bondable') and obj.Bondable:
            # This is a bondable layer from GDS import
            die_pads.append(obj)
        elif 'Lead' in obj.Label or 'Finger' in obj.Label or 'Pad' in obj.Label:
            # Leadframe leads/pads can act as bond fingers
            bond_fingers.append(obj)
        elif hasattr(obj, 'Shape') and obj.Shape.Faces:
            # Any face-containing object could potentially be bondable
            if 'Bond' in obj.Label or 'Pad' in obj.Label:
                if 'Die' in obj.Label:
                    die_pads.append(obj)
                else:
                    bond_fingers.append(obj)
    
    return die_pads, bond_fingers


def create_bond_wire_3d(start_point, end_point, config, net_name="Unknown"):
    """Create a 3D bond wire with realistic loop height and cross-section."""
    try:
        loop_height = config["loop_height"]
        diameter = config["diameter"]
        
        # Calculate intermediate points for realistic wire arc
        horizontal_dist = math.sqrt((end_point.x - start_point.x)**2 + (end_point.y - start_point.y)**2)
        
        # Simple fallback for very short distances
        if horizontal_dist < 0.001:
            return Part.makeCylinder(diameter/2, 0.1)  # Minimum 0.1mm wire
        
        # Use multiple intermediate points for smooth curve
        num_points = 7  # More points for smoother curve
        points = []
        
        for i in range(num_points):
            t = i / (num_points - 1)  # Parameter from 0 to 1
            
            # Linear interpolation in X and Y
            x = start_point.x + t * (end_point.x - start_point.x)
            y = start_point.y + t * (end_point.y - start_point.y)
            
            # Parabolic arc in Z (loop height)
            base_z = start_point.z + t * (end_point.z - start_point.z)
            # Maximum loop at t=0.5, with smooth transition
            arc_height = loop_height * 4 * t * (1 - t)  # Parabolic profile
            z = base_z + arc_height
            
            points.append(Vector(x, y, z))
        
        # Create simple wire as line with circular cross-section
        try:
            # Create BSpline curve
            spline = Part.BSplineCurve()
            spline.interpolate(points)
            spine = spline.toShape()
            
            # Create a simple tube around the spine
            wire_solid = spine.makeTube(diameter/2)
            
            if wire_solid and wire_solid.isValid():
                return wire_solid
            else:
                raise Exception("Tube creation failed")
                
        except Exception:
            # Ultimate fallback: straight cylinder
            direction = end_point.sub(start_point)
            length = direction.Length
            if length > 0:
                return Part.makeCylinder(diameter/2, length)
            else:
                return Part.makeCylinder(diameter/2, 0.1)
        
    except Exception as e:
        FreeCAD.Console.PrintError(f"Error creating 3D bond wire: {str(e)}\n")
        # Create simple straight wire as fallback
        return Part.makeCylinder(config["diameter"]/2, 1.0)


def create_wire_bonds(config=None, connections=None):
    """Main function to create wire bonds in the current document."""
    doc = FreeCAD.activeDocument()
    if not doc:
        QtWidgets.QMessageBox.critical(None, "Error", "No active document found.")
        return None, []
    
    # Find bondable objects
    die_pads, bond_fingers = find_bondable_objects()
    
    if not die_pads:
        QtWidgets.QMessageBox.warning(None, "Warning", 
            "No die pads found. Import GDS layers with bondable pads first.")
        return None, []
    
    if not bond_fingers:
        QtWidgets.QMessageBox.warning(None, "Warning", 
            "No bond fingers found. Create leadframe with leads/pads first.")
        return None, []
    
    # Get wire bonding configuration
    if not config:
        config_dialog = WirebondConfigurator()
        if config_dialog.exec_():
            config = config_dialog.get_config()
        else:
            return None, []
    
    # Get wire bond connections
    if not connections:
        selector = BondWireSelector(die_pads, bond_fingers)
        if selector.exec_():
            connections = selector.get_connections()
        else:
            return None, []
    
    if not connections:
        QtWidgets.QMessageBox.information(None, "Info", "No connections selected.")
        return None, []
    
    # Create wire bonds
    try:
        doc.openTransaction("Create Wire Bonds")
    except Exception:
        pass
    
    created_wires = []
    
    for i, (die_pad, bond_finger, net_name) in enumerate(connections):
        try:
            # Get connection points using safe center calculation
            die_center = get_shape_center(die_pad)
            finger_center = get_shape_center(bond_finger)
            
            # Adjust Z positions
            die_z = die_center.z + 0.01  # Slightly above die pad
            finger_z = finger_center.z + 0.01  # Slightly above bond finger
            
            start_point = Vector(die_center.x, die_center.y, die_z)
            end_point = Vector(finger_center.x, finger_center.y, finger_z)
            
            # Create 3D wire
            wire_shape = create_bond_wire_3d(start_point, end_point, config, net_name)
            
            # Create FreeCAD object
            wire_obj = doc.addObject("Part::Feature", f"BondWire_{i+1}")
            wire_obj.Shape = wire_shape
            wire_obj.Placement = Base.Placement(start_point, Base.Rotation())
            
            # Set visual properties (gold color for bond wires)
            wire_obj.ViewObject.ShapeColor = (0.90, 0.75, 0.20)  # Gold
            wire_obj.ViewObject.LineColor = (0.25, 0.20, 0.10)   # Dark gold
            wire_obj.ViewObject.LineWidth = 2.0
            
            # Add custom properties
            wire_obj.addProperty("App::PropertyString", "Net", "WireBond", "Net name")
            wire_obj.Net = net_name
            
            wire_obj.addProperty("App::PropertyString", "DiePad", "WireBond", "Source die pad")
            wire_obj.DiePad = die_pad.Label
            
            wire_obj.addProperty("App::PropertyString", "BondFinger", "WireBond", "Target bond finger")
            wire_obj.BondFinger = bond_finger.Label
            
            wire_obj.addProperty("App::PropertyFloat", "LoopHeight", "WireBond", "Wire loop height")
            wire_obj.LoopHeight = config["loop_height"]
            
            wire_obj.addProperty("App::PropertyFloat", "Diameter", "WireBond", "Wire diameter")
            wire_obj.Diameter = config["diameter"]
            
            created_wires.append(wire_obj)
            
        except Exception as e:
            FreeCAD.Console.PrintError(f"Failed to create wire bond {i+1}: {str(e)}\n")
            continue
    
    # Run design rule checks
    if created_wires:
        drc = WireBondDesignRuleChecker(config)
        violations = drc.check_all_rules(created_wires, die_pads, bond_fingers)
        
        if violations:
            # Show violations dialog
            show_drc_violations(violations)
    
    try:
        doc.commitTransaction()
    except Exception:
        pass
    
    doc.recompute()
    FreeCADGui.SendMsgToActiveView("ViewFit")
    
    return doc, created_wires


def show_drc_violations(violations):
    """Show design rule violation results."""
    dialog = QtWidgets.QDialog()
    dialog.setWindowTitle("Wire Bonding Design Rule Check Results")
    dialog.setMinimumSize(600, 400)
    
    layout = QtWidgets.QVBoxLayout()
    
    if not violations:
        label = QtWidgets.QLabel("✅ All design rules passed!")
        label.setStyleSheet("color: green; font-weight: bold;")
        layout.addWidget(label)
    else:
        label = QtWidgets.QLabel(f"Found {len(violations)} design rule violations:")
        layout.addWidget(label)
        
        # Create table for violations
        table = QtWidgets.QTableWidget()
        table.setColumnCount(3)
        table.setHorizontalHeaderLabels(["Type", "Severity", "Message"])
        table.setRowCount(len(violations))
        
        for row, violation in enumerate(violations):
            table.setItem(row, 0, QtWidgets.QTableWidgetItem(violation['type']))
            
            severity_item = QtWidgets.QTableWidgetItem(violation['severity'])
            if violation['severity'] == 'Error':
                severity_item.setBackground(QtGui.QColor(255, 200, 200))
            elif violation['severity'] == 'Warning':
                severity_item.setBackground(QtGui.QColor(255, 255, 200))
            
            table.setItem(row, 1, severity_item)
            table.setItem(row, 2, QtWidgets.QTableWidgetItem(violation['message']))
        
        table.resizeColumnsToContents()
        layout.addWidget(table)
    
    # Close button
    close_btn = QtWidgets.QPushButton("Close")
    close_btn.clicked.connect(dialog.accept)
    layout.addWidget(close_btn)
    
    dialog.setLayout(layout)
    dialog.exec_()


def generate_wire_bonding_report():
    """Generate a wire bonding table report."""
    doc = FreeCAD.activeDocument()
    if not doc:
        QtWidgets.QMessageBox.critical(None, "Error", "No active document found.")
        return
    
    # Find all wire bond objects
    wires = [obj for obj in doc.Objects if obj.Label.startswith("BondWire")]
    
    if not wires:
        QtWidgets.QMessageBox.information(None, "Info", "No wire bonds found in document.")
        return
    
    # Get save location
    filename, _ = QtWidgets.QFileDialog.getSaveFileName(
        None, "Save Wire Bonding Report", "wire_bonding_report.csv", "CSV Files (*.csv)"
    )
    
    if not filename:
        return
    
    # Generate report
    try:
        import csv
        
        with open(filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                "Wire ID", "Net", "Die Pad", "Bond Finger", 
                "Length (mm)", "Loop Height (mm)", "Diameter (mm)"
            ])
            
            for i, wire in enumerate(wires):
                net = getattr(wire, 'Net', f'Net_{i+1}')
                die_pad = getattr(wire, 'DiePad', 'Unknown')
                bond_finger = getattr(wire, 'BondFinger', 'Unknown')
                length = wire.Shape.Length if hasattr(wire, 'Shape') else 0
                loop_height = getattr(wire, 'LoopHeight', 0)
                diameter = getattr(wire, 'Diameter', 0)
                
                writer.writerow([
                    wire.Label, net, die_pad, bond_finger,
                    f"{length:.3f}", f"{loop_height:.3f}", f"{diameter:.3f}"
                ])
        
        QtWidgets.QMessageBox.information(
            None, "Success", f"Wire bonding report saved to:\n{filename}"
        )
        
    except Exception as e:
        QtWidgets.QMessageBox.critical(
            None, "Error", f"Failed to save report:\n{str(e)}"
        )


class WirebondCommand:
    """FreeCAD command for wire bonding functionality."""
    
    def GetResources(self):
        icon_path = os.path.join(os.path.dirname(__file__), "resources", "icons", "Wire Bonding.png")
        return {
            "MenuText": "Wire Bonding",
            "ToolTip": "Create wire bonds between die pads and bond fingers with design rule checking",
            "Pixmap": icon_path
        }
    
    def Activated(self):
        result = create_wire_bonds()
        if result and result[1]:  # Check if wires were created
            doc, wires = result
            QtWidgets.QMessageBox.information(
                None, "Success", 
                f"Created {len(wires)} wire bonds successfully.\n"
                "Design rule check results are displayed if violations were found."
            )
        else:
            QtWidgets.QMessageBox.information(None, "Info", "Wire bonding cancelled or no wires created.")
    
    def IsActive(self):
        return FreeCAD.activeDocument() is not None
    

FreeCADGui.addCommand('WirebondCommand', WirebondCommand())