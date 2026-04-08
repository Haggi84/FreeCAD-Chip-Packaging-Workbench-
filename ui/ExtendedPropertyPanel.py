from gds.PropertyPanel import PropertyPanel
from PySide2 import QtWidgets
import FreeCAD, FreeCADGui, os, sys

root_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root_path)

from core.Core_Functionality import parse_lyp

# Extend PropertyPanel to support leadframe modification, wire bonding modification
class ExtendedPropertyPanel(PropertyPanel):
    def __init__(self, parent=None):
        super().__init__(parent)
        # Add leadframe_config and transform_opts
        self.modify_layers_button.setText("Modify Leadframe, Layers or Wire Bonds")
        self.leadframe_config = None
        self.transform_opts = None
        self.wire_bonding_config = None

    def modify_layer_selection(self):
        from ui.LayerSelector import LayerSelector
        from leadframe.LeadframeCommand import configure_leadframe
        from leadframe.LayeronLeadframe import configuration
        from wirebond.ManualWireBonding import manual_bonder  # Global instance
        from wirebond.WirebondConfigurator import WirebondConfigurator

        """Override to allow modifying either layer selection or leadframe configuration."""
        if not self.gds_path:
            QtWidgets.QMessageBox.critical(None, "Error", "Cannot modify: Missing file paths or layer data.")
            return

        # Prompt user to choose what to modify
        choice_dialog = QtWidgets.QDialog(self)
        choice_dialog.setWindowTitle("Modify Selection")
        layout = QtWidgets.QVBoxLayout()
        message = QtWidgets.QLabel("What would you like to modify?")
        layout.addWidget(message)
        layer_button = QtWidgets.QPushButton("Modify Layer Selection")
        leadframe_button = QtWidgets.QPushButton("Modify Leadframe Configuration")
        wire_bonds_button = QtWidgets.QPushButton("Modify Wire Bonds")
        cancel_button = QtWidgets.QPushButton("Cancel")
        layout.addWidget(layer_button)
        layout.addWidget(leadframe_button)
        layout.addWidget(wire_bonds_button)
        layout.addWidget(cancel_button)
        choice_dialog.setLayout(layout)

        modify_type = [None]  # Use list to allow modification in slots

        def set_layer_modification():
            modify_type[0] = "layers"
            choice_dialog.accept()

        def set_leadframe_modification():
            modify_type[0] = "leadframe"
            choice_dialog.accept()

        def set_wire_bonds_modification():
            modify_type[0] = "wire_bonds"
            choice_dialog.accept()

        layer_button.clicked.connect(set_layer_modification)
        leadframe_button.clicked.connect(set_leadframe_modification)
        wire_bonds_button.clicked.connect(set_wire_bonds_modification)
        cancel_button.clicked.connect(choice_dialog.reject)

        if choice_dialog.exec_() != QtWidgets.QDialog.Accepted or not modify_type[0]:
            QtWidgets.QMessageBox.information(None, "Cancelled", "Modification cancelled.")
            return

        doc = FreeCAD.activeDocument()
        if not doc:
            QtWidgets.QMessageBox.critical(None, "Error", "No active document found.")
            return

        try:
            doc.openTransaction("Update Layer or Leadframe")
        except Exception:
            pass

        if modify_type[0] == "layers":
            # Modify layer selection (similar to original PropertyPanel logic)
            dialog = LayerSelector(self.filtered_layers, self.selected_layers, options=self.options)
            if dialog.exec_():
                selected_layers = dialog.selected_layers
                options = dialog.options
                if not selected_layers:
                    QtWidgets.QMessageBox.warning(None, "Warning", "No layers selected.")
                    return

                self.options = dict(options)

                # Clear existing objects
                for obj in doc.Objects:
                    try:
                        doc.removeObject(obj.Name)
                    except Exception as e:
                        FreeCAD.Console.PrintError(f"Error removing object {obj.Name}: {str(e)}\n")

                result = configuration(doc, self.gds_path, selected_layers, self.options, self.ihp_map, self.leadframe_config, self.transform_opts)
                if not result:
                    QtWidgets.QMessageBox.warning(None, "Warning", "Failed to create layer on leadframe.")
                    return

                doc, layer_objects = result

                self.selected_layers = selected_layers

                if self.lyp_path:
                    unique_colors = parse_lyp(self.lyp_path)[1]

                else:
                    unique_colors = set()
                    for layer in self.selected_layers:
                        frame_color = layer.get("frame-color", "#000000")
                        fill_color = layer.get("fill-color", "#FFFFFF")
                    unique_colors.add((frame_color, fill_color))

                # Update PropertyPanel
                self.update_properties(self.selected_layers, unique_colors, layer_objects)

        elif modify_type[0] == "leadframe":
            # Modify leadframe configuration
            new_config = configure_leadframe()
            if not new_config:
                QtWidgets.QMessageBox.information(None, "Cancelled", "Leadframe configuration cancelled.")
                return

            # Update leadframe config
            self.leadframe_config = new_config

            # Clear existing objects
            for obj in doc.Objects:
                try:
                    doc.removeObject(obj.Name)
                except Exception as e:
                    FreeCAD.Console.PrintError(f"Error removing object {obj.Name}: {str(e)}\n")

            result = configuration(doc, self.gds_path, self.selected_layers, self.options, self.ihp_map, self.leadframe_config, self.transform_opts)
            if not result:
                QtWidgets.QMessageBox.warning(None, "Warning", "Failed to create layer on leadframe.")
                return

            doc, layer_objects = result

            if self.lyp_path:
                unique_colors = parse_lyp(self.lyp_path)[1]

            else:
                unique_colors = set()
                for layer in self.selected_layers:
                    frame_color = layer.get("frame-color", "#000000")
                    fill_color = layer.get("fill-color", "#FFFFFF")
                    unique_colors.add((frame_color, fill_color))

            # Update PropertyPanel
            self.update_properties(self.selected_layers, unique_colors, layer_objects)

        elif modify_type[0] == "wire_bonds":
            dialog = WirebondConfigurator()
            if dialog.exec_():
                new_config = dialog.get_config()

                for obj in doc.Objects:
                    if obj.Label.startswith("BondWire"):
                        doc.removeObject(obj.Name)

                # Add 2D-specific settings
                new_config['wire_style'] = 'arc'
                new_config['arc_height'] = new_config.get('loop_height', 0.5)

                manual_bonder.start_bonding_session(new_config)

                #Show instructions
                QtWidgets.QMessageBox.information(None, "Manual 2D Wire Bonding", 
                    "Manual 2D Wire Bonding Started!\n\n"
                    "INSTRUCTIONS:\n"
                    "1. Click on START point (die pad)\n"
                    "2. Click on END point (bond finger)\n" 
                    "3. Repeat for each bond\n"
                    "4. Use 'Finish Wire Bonding' command to complete\n\n"
                    "Look for green/red temporary markers!")
            else:
                QtWidgets.QMessageBox.information(None, "Cancelled", "Wire bonds configuration cancelled.")
                return

        try:
            doc.commitTransaction()
        except Exception:
            pass

        doc.recompute()
        FreeCADGui.activeDocument().activeView().viewIsometric()
        FreeCADGui.SendMsgToActiveView("ViewFit")
        QtWidgets.QMessageBox.information(None, "Success", f"{'Layer selection' if modify_type[0] == 'layers' else 'Leadframe configuration' if modify_type[0] == 'leadframe' else 'Wire bonds'} updated successfully.")