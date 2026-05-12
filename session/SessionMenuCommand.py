"""
Combined Save / Load session dropdown command.

When activated it pops up a small menu at the cursor with
"Save Design Session" and "Load Design Session" items.
"""

import FreeCAD
import FreeCADGui
from compat import QtWidgets, QtGui

from Get_Path import get_icon
from session.SessionManager import session_manager


class SessionMenuCommand:
    def GetResources(self):
        return {
            "MenuText": "Session",
            "ToolTip":  "Save or load a design session (.dipas file)",
            "Pixmap":   get_icon("Session.svg"),
        }

    def Activated(self):
        menu = QtWidgets.QMenu()

        save_act = menu.addAction(
            QtGui.QIcon(get_icon("Save_Session.svg")),
            "Save Design Session",
        )
        load_act = menu.addAction(
            QtGui.QIcon(get_icon("Load_Session.svg")),
            "Load Design Session",
        )

        action = menu.exec_(QtGui.QCursor.pos())

        if action == save_act:
            from session.SaveSessionCommand import SaveSessionCommand
            SaveSessionCommand().Activated()
        elif action == load_act:
            from session.LoadSessionCommand import LoadSessionCommand
            LoadSessionCommand().Activated()

    def IsActive(self):
        return True


FreeCADGui.addCommand("SessionMenuCommand", SessionMenuCommand())
