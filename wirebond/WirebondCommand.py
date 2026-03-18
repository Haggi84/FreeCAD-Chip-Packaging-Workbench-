import os
import sys

import FreeCAD
import FreeCADGui
from PySide2 import QtWidgets

root_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root_path)

from wirebond.WirebondConfigurator import WirebondConfigurator
from wirebond.ManualWireBonding import manual_bonder  # Global instance
from wirebond.Wirebon_Confi_Support import check_wirebond_prerequisites
from wirebond.ContactPointTool import DefineContactPointsCommand
from Get_Path import get_icon


class WirebondCommand:
    def GetResources(self):
        return {
            "MenuText": "Manual 2D Wire Bonding",
            "ToolTip": "Manually create 2D wire bonds by selecting points",
            "Pixmap": get_icon("Wire_bonding.png"),
        }

    def Activated(self):
        # Check prerequisites
        can_bond, message = check_wirebond_prerequisites()

        if not can_bond:
            QtWidgets.QMessageBox.warning(
                None,
                "Wire Bonding Not Available",
                f"{message}\n\n"
                "Wire bonding requires:\n"
                "• GDS layers with bondable pads (use 'Load GDSII')\n"
                "• A leadframe (use 'Leadframe Configurator' or 'Layer on Leadframe')",
            )
            return

        # Show configuration dialog
        dialog = WirebondConfigurator()
        if dialog.exec_():
            config = dialog.get_config()

            if not FreeCAD.activeDocument():
                FreeCAD.newDocument("WireBonding")

            manual_bonder.start_bonding_session(config)

            QtWidgets.QMessageBox.information(
                None,
                "Wire Bonding Started",
                "Wire bonding session active.\n\n"
                "Only ContactPoint markers can be selected.\n\n"
                "1. Click a ContactPoint on the die pad.\n"
                "2. Click a ContactPoint on the leadframe lead.\n"
                "3. A 3-D bond wire is created between them.\n"
                "4. Repeat for each bond.\n"
                "5. Click 'Finish Wire Bonding' when done.\n\n"
                "ContactPoints appear as coloured dots:\n"
                "  Orange = die side\n"
                "  Blue   = leadframe side",
            )
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
        }

    def Activated(self):
        bond_count = manual_bonder.finish_session()
        QtWidgets.QMessageBox.information(
            None,
            "Finished",
            f"Manual wire bonding completed!\n\n"
            f"Created {bond_count} bond wires.\n"
            f"Check report in Python Console.",
        )

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
FreeCADGui.addCommand("WirebondCommand", WirebondCommand())
FreeCADGui.addCommand("FinishWireBondingCommand", FinishWireBondingCommand())
FreeCADGui.addCommand("CancelWireBondingCommand", CancelWireBondingCommand())
FreeCADGui.addCommand("DefineContactPointsCommand", DefineContactPointsCommand())
