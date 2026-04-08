import FreeCAD, FreeCADGui, os, sys
from PySide2 import QtWidgets

root_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root_path)

from wirebond.WirebondConfigurator import WirebondConfigurator
from wirebond.ManualWireBonding import manual_bonder  # Global instance
from wirebond.Wirebon_Confi_Support import check_wirebond_prerequisites
from Get_Path import get_icon

Wire_Bonding_Active = False  # Global flag to track bonding session state

class WirebondCommand:
    def GetResources(self):
        return {
            "MenuText": "Manual 2D Wire Bonding",
            "ToolTip": "Manually create 2D wire bonds by selecting points",
            "Pixmap": get_icon("Wire_bonding.png")
        }

    def Activated(self):
        global Wire_Bonding_Active

        # Check prerequisites
        can_bond, message = check_wirebond_prerequisites()
        
        if not can_bond:
            QtWidgets.QMessageBox.warning(
                None, 
                "Wire Bonding Not Available", 
                f"{message}\n\n"
                "Wire bonding requires:\n"
                "• GDS layers with bondable pads (use 'Load GDSII')\n"
                "• A leadframe (use 'Leadframe Configurator' or 'Layer on Leadframe')"
            )
            return
        

        # Show configuration dialog
        dialog = WirebondConfigurator()
        if dialog.exec_():
            config = dialog.get_config()
            
            # Add 2D-specific settings
            config['wire_style'] = 'arc' 
            config['arc_height'] = config.get('loop_height', 0.5)
            
            # Ensure we have a document
            doc = FreeCAD.activeDocument()
            if not doc:
                doc = FreeCAD.newDocument("2D_WireBonding")
            
            # Start manual bonding using the global instance
            manual_bonder.start_bonding_session(config)

            Wire_Bonding_Active = True
            
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
        """
        Command is only active if prerequisites are met.
        This grays out the button when conditions aren't met.
        """
        can_bond, _ = check_wirebond_prerequisites()
        return can_bond

class FinishWireBondingCommand:
    def GetResources(self):
        return {
            "MenuText": "Finish Wire Bonding",
            "ToolTip": "Finish manual wire bonding session and generate report",
            "Pixmap": get_icon("Finish_Wire_bonding.png")
        }

    def Activated(self):
        global Wire_Bonding_Active
        
        bond_count = manual_bonder.finish_session()

        Wire_Bonding_Active = False

        QtWidgets.QMessageBox.information(None, "Finished", 
            f"Manual wire bonding completed!\n\n"
            f"Created {bond_count} bond wires.\n"
            f"Check report in Python Console.")

    def IsActive(self):
        global Wire_Bonding_Active
        return Wire_Bonding_Active

class CancelWireBondingCommand:
    def GetResources(self):
        return {
            "MenuText": "Cancel Wire Bonding", 
            "ToolTip": "Cancel current wire bonding session",
            "Pixmap": get_icon("Cancel_Wire_bonding.png")
        }

    def Activated(self):
        global Wire_Bonding_Active

        manual_bonder.cancel_session()

        Wire_Bonding_Active = False
        
        QtWidgets.QMessageBox.information(None, "Cancelled", "Wire bonding session cancelled.")

    def IsActive(self):
        global Wire_Bonding_Active
        return Wire_Bonding_Active

# Register commands
FreeCADGui.addCommand('WirebondCommand', WirebondCommand())
FreeCADGui.addCommand('FinishWireBondingCommand', FinishWireBondingCommand()) 
FreeCADGui.addCommand('CancelWireBondingCommand', CancelWireBondingCommand())