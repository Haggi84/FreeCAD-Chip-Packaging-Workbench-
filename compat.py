"""
PySide-Kompatibilitäts-Shim für DI-PASSIONATE.

Unterstützt FreeCAD < 1.0  (PySide2 / Qt 5)
        und FreeCAD >= 1.0  (PySide6 / Qt 6)

Verwendung in allen Modulen:
    from compat import QtWidgets, QtCore, QtGui
statt:
    from PySide2 import QtWidgets, QtCore, QtGui
"""

try:
    from PySide6 import QtWidgets, QtCore, QtGui       # FreeCAD >= 1.0
    _PYSIDE_VERSION = 6

    # ── exec_() wurde in PySide6 entfernt ─────────────────────────────────
    def _patch_exec(cls):
        if not hasattr(cls, "exec_"):
            cls.exec_ = cls.exec

    _patch_exec(QtWidgets.QDialog)
    _patch_exec(QtWidgets.QMenu)
    _patch_exec(QtWidgets.QApplication)

    # ── QShortcut / QAction: QtWidgets → QtGui in PySide6 ─────────────────
    if not hasattr(QtWidgets, "QShortcut"):
        QtWidgets.QShortcut = QtGui.QShortcut
    if not hasattr(QtWidgets, "QAction"):
        QtWidgets.QAction = QtGui.QAction

except ImportError:
    from PySide2 import QtWidgets, QtCore, QtGui       # FreeCAD < 1.0
    _PYSIDE_VERSION = 2


def qenum_int(value) -> int:
    """
    Qt-Enum-Wert sicher in int umwandeln.

    PySide2: int(enum)          funktioniert direkt.
    PySide6: scoped enums – int() schlägt fehl, .value liefert den int.

    Verwendung (statt int(QtWidgets.QDialogButtonBox.Ok | ...)):
        qenum_int(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
    """
    try:
        return int(value)
    except TypeError:
        return int(value.value)


__all__ = ["QtWidgets", "QtCore", "QtGui", "_PYSIDE_VERSION", "qenum_int"]
