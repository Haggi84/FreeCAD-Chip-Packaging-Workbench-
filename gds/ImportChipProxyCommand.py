# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Import Chip (Layout Proxy) command.

Fast alternative to "Load GDSII" for layout/placement work: extracts only
the die's footprint, real stack thickness, and bond-pad positions (see
core.chip_proxy) and builds one lightweight block + ContactPoint markers —
skipping the full per-layer OCCT tessellation entirely, so this stays fast
regardless of how large or how many chips are involved.

LYP/MAP/XML are all optional here (unlike the full "Load GDSII" flow, which
needs the LYP for its layer-selection/colour pipeline): footprint comes
straight from the raw GDS, and pad detection degrades gracefully without
a map file — LYP and MAP only sharpen pad naming/detection strategy, XML
only sharpens the thickness estimate.

The technology is chosen per import and recorded on the chip (core.gds_tech),
so several dies from different PDKs can sit in one package and each still
answers for itself.
"""

import os
import sys

import FreeCAD
import FreeCADGui
from compat import QtWidgets

root_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root_path)

from core.chip_proxy import extract_chip_proxy, build_chip_proxy_object
from Get_Path import get_icon


class ImportChipProxyCommand:
    def GetResources(self):
        return {
            "MenuText": "Import Chip (Layout Proxy)",
            "ToolTip": (
                "Fast layout-stage chip import: footprint + real stack "
                "thickness + bond-pad positions only — no per-layer routing\n"
                "geometry, no OCCT tessellation. Promote to full detail "
                "later once placement is settled."
            ),
            "Pixmap": get_icon("Import_Chip_Proxy.svg"),
        }

    def Activated(self):
        gds_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            None, "Select GDS File", "", "GDS Files (*.gds *.GDS)"
        )
        if not gds_path or not os.path.exists(gds_path):
            return

        # Which PDK this chip is made in is asked per import, not taken from
        # the session: the next die in the same package is routinely from a
        # different process, and the answer is recorded on the chip.
        from ui.TechnologyDialog import choose_technology
        technology = choose_technology(FreeCADGui.getMainWindow(), gds_path)
        if technology is None:
            FreeCAD.Console.PrintMessage("[ChipProxy] Import cancelled.\n")
            return
        lyp_path = technology["lyp_path"] or None
        map_path = technology["map_path"] or None
        xml_path = technology["xml_path"] or None

        doc = FreeCAD.activeDocument()
        if doc is None:
            doc = FreeCAD.newDocument("ChipLayout")

        name = os.path.splitext(os.path.basename(gds_path))[0]

        try:
            proxy_data = extract_chip_proxy(gds_path, lyp_path, map_path, xml_path)

            # Confirm the outer box BEFORE building it. A proxy keeps nothing
            # but its dimensions, and thickness in particular cannot be read
            # from a GDS at all — warning about that after the block already
            # exists leaves the user to fix it by hand.
            from ui.ChipDimensionsDialog import ChipDimensionsDialog
            dlg = ChipDimensionsDialog(proxy_data, FreeCADGui.getMainWindow())
            if dlg.exec_() != QtWidgets.QDialog.Accepted:
                FreeCAD.Console.PrintMessage("[ChipProxy] Import cancelled.\n")
                return
            proxy_data = dlg.apply_to(proxy_data)

            block = build_chip_proxy_object(doc, proxy_data, name=name)
            import core.gds_tech as gds_tech
            gds_tech.tag(block, technology)
        except Exception as exc:
            import traceback
            FreeCAD.Console.PrintError(
                f"[ChipProxy] Import failed: {exc}\n{traceback.format_exc()}\n"
            )
            QtWidgets.QMessageBox.critical(None, "Import Failed", str(exc))
            return

        if FreeCAD.GuiUp:
            FreeCADGui.activeDocument().activeView().viewIsometric()
            FreeCADGui.SendMsgToActiveView("ViewFit")

        w, h = (proxy_data["footprint_mm"][2] - proxy_data["footprint_mm"][0],
                 proxy_data["footprint_mm"][3] - proxy_data["footprint_mm"][1])
        # block.Label reflects any de-duplication suffix build_chip_proxy_object
        # applied (e.g. importing the same chip twice) — read it back rather
        # than the raw requested `name` so this message matches the tree.
        shown_name = block.Label.replace(" Die Block", "")
        msg = (
            f"Chip proxy '{shown_name}' created:\n"
            f"  Width  (X): {w:.4f} mm\n"
            f"  Length (Y): {h:.4f} mm\n"
            f"  Thickness:  {proxy_data['thickness_mm']:.4f} mm\n"
            f"  Bond pads:  {len(proxy_data['pads'])}\n"
            f"\nOutline:   {proxy_data.get('footprint_source', 'unknown')}"
            f"\nThickness: {proxy_data.get('thickness_source', 'unknown')}"
        )
        QtWidgets.QMessageBox.information(None, "Chip Proxy Imported", msg)

        # No pads means nothing to bond to, and it is not always a broken
        # file: a layout whose pads follow none of the conventions automatic
        # detection knows (SKY130's, for one) imports clean and empty. The
        # layout still knows where its pads are, so offer to point at them
        # now rather than leaving the chip unusable until someone works out
        # that a separate command exists.
        if not proxy_data.get("pads"):
            answer = QtWidgets.QMessageBox.question(
                None, "No bond pads found",
                "No bond pads were detected in this layout.\n\n"
                "That happens when its pads are not drawn the way automatic "
                "detection expects — SKY130 layouts, for instance, keep their "
                "pad openings on a layer it does not recognise.\n\n"
                "Pick them out of the file now?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.Yes)
            if answer == QtWidgets.QMessageBox.Yes:
                from gds.PadPickerCommand import define_pads
                define_pads(doc, block, gds_path)

    def IsActive(self):
        return True


if FreeCAD.GuiUp:
    FreeCADGui.addCommand("ImportChipProxyCommand", ImportChipProxyCommand())
