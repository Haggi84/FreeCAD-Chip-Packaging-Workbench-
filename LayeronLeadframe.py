from PySide2 import QtWidgets
import FreeCAD
import FreeCADGui
import LeadframeCommand
import GDSCommand

class LayeronLeadframe:
    def GetResources(self):
        return {
            "MenuText": "Layer on Leadframe",
            "ToolTip": "Configure layers on leadframe",
            "Pixmap": ""
        }
    
    def Activated(self):
        try:
            # Step 1: Load the GDS file and get layers
            doc, layer_objects, selected_layers, unique_colors = GDSCommand.load_gds_layers()
            if not doc or not layer_objects:
                FreeCAD.Console.PrintError("❌ Failed to load GDS layers.\n")
                return
            
            # Step 2: Create a leadframe with the selected layers
            config = LeadframeCommand.configure_leadframe()
            if not config:
                FreeCAD.Console.PrintError("❌ Leadframe configuration failed.\n")
                return
            
            # Step 3: Create the leadframe with GDS objects
            LeadframeCommand.create_leadframe(config, doc, layer_objects)

            QtWidgets.QMessageBox.information(
                None,
                "Success",
                "Leadframe created with GDS layers successfully."
            )

        except Exception as e:
            FreeCAD.Console.PrintError(f"❌ An error occurred: {str(e)}\n")
            QtWidgets.QMessageBox.critical(
                None,
                "Error",
                f"An error occurred while creating the leadframe: {str(e)}"
            )

    def IsActive(self):
        """
        Check if the command is active.
        """
        return True

FreeCADGui.addCommand("LayeronLeadframe", LayeronLeadframe())