from gds.PropertyPanel import PropertyPanel
from PySide2 import QtWidgets
import FreeCAD, FreeCADGui
from core import Core_Functionality

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
        from leadframe import LeadframeCommand
        from gds.GDSCommand import configuration
        from wirebond import WirebondCommand
        from wirebond.WirebondConfigurator import WirebondConfigurator

        """Override to allow modifying either layer selection or leadframe configuration."""
        if not self.gds_path or not self.lyp_path:
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

                # Update PropertyPanel
                self.selected_layers = selected_layers
                self.update_properties(self.selected_layers, Core_Functionality.parse_lyp(self.lyp_path)[1], layer_objects)

        elif modify_type[0] == "leadframe":
            # Modify leadframe configuration
            new_config = LeadframeCommand.configure_leadframe()
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

            # Update PropertyPanel
            self.update_properties(self.selected_layers, Core_Functionality.parse_lyp(self.lyp_path)[1], layer_objects)

        elif modify_type[0] == "wire_bonds":
            dialog = WirebondConfigurator()
            if dialog.exec_():
                new_config = dialog.get_config()
                self.wire_bond_config = new_config
                for obj in doc.Objects:
                    if obj.Label.startswith("BondWire"):
                        doc.removeObject(obj.Name)
                result = WirebondCommand.create_wire_bonds(new_config, self.layer_objects, self.leadframe_config)
                if not result:
                    QtWidgets.QMessageBox.warning(None, "Warning", "Failed to create wire bonds.")
                    return
                doc, wires = result
                QtWidgets.QMessageBox.information(None, "Success", "Wire bonds updated successfully.")
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