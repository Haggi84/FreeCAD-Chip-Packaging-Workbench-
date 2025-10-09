import FreeCAD, FreeCADGui, os
from PySide2 import QtWidgets
from All_Class import WirebondConfigurator, manual_bonder  # Import from All_Class

class WirebondCommand:
    def GetResources(self):
        icon_path = os.path.join(os.path.dirname(__file__), "resources", "icons", "Wire_bonding.png")
        return {
            "MenuText": "Manual 2D Wire Bonding",
            "ToolTip": "Manually create 2D wire bonds by selecting points",
            "Pixmap": icon_path
        }

    def Activated(self):
        # Show configuration dialog
        dialog = WirebondConfigurator()
        if dialog.exec_():
            config = dialog.get_config()
            
            # Add 2D-specific settings
            config['wire_style'] = 'straight'  # Options: 'straight' or 'arc'
            config['arc_height'] = 2.0
            
            # Ensure we have a document
            doc = FreeCAD.activeDocument()
            if not doc:
                doc = FreeCAD.newDocument("2D_WireBonding")
            
            # Start manual bonding using the global instance
            manual_bonder.start_bonding_session(config)
            
            # Show instructions
            QtWidgets.QMessageBox.information(None, "Manual 2D Wire Bonding", 
                "Manual 2D Wire Bonding Started!\n\n"
                "INSTRUCTIONS:\n"
                "1. Click on START point (die pad)\n"
                "2. Click on END point (bond finger)\n" 
                "3. Repeat for each bond\n"
                "4. Use 'Finish Wire Bonding' command to complete\n\n"
                "Look for green/red temporary markers!")
        else:
            QtWidgets.QMessageBox.information(None, "Cancelled", "Wire bonding configuration cancelled.")

    def IsActive(self):
        return True

class FinishWireBondingCommand:
    def GetResources(self):
        return {
            "MenuText": "Finish Wire Bonding",
            "ToolTip": "Finish manual wire bonding session and generate report",
        }

    def Activated(self):
        bond_count = manual_bonder.finish_session()
        QtWidgets.QMessageBox.information(None, "Finished", 
            f"Manual wire bonding completed!\n\n"
            f"Created {bond_count} bond wires.\n"
            f"Check report in Python Console.")

    def IsActive(self):
        return True

class CancelWireBondingCommand:
    def GetResources(self):
        return {
            "MenuText": "Cancel Wire Bonding", 
            "ToolTip": "Cancel current wire bonding session",
        }

    def Activated(self):
        manual_bonder.cancel_session()
        QtWidgets.QMessageBox.information(None, "Cancelled", "Wire bonding session cancelled.")

    def IsActive(self):
        return True

# Register commands
FreeCADGui.addCommand('WirebondCommand', WirebondCommand())
FreeCADGui.addCommand('FinishWireBondingCommand', FinishWireBondingCommand()) 
FreeCADGui.addCommand('CancelWireBondingCommand', CancelWireBondingCommand())