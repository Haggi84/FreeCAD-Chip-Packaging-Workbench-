# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
PySide compatibility shim for DI-PASSIONATE.

Supports FreeCAD < 1.0  (PySide2 / Qt 5)
      and FreeCAD >= 1.0  (PySide6 / Qt 6)

Usage in all modules:
    from compat import QtWidgets, QtCore, QtGui
instead of:
    from PySide2 import QtWidgets, QtCore, QtGui
"""

try:
    from PySide6 import QtWidgets, QtCore, QtGui       # FreeCAD >= 1.0
    _PYSIDE_VERSION = 6

    # ── exec_() was removed in PySide6 ───────────────────────────────────
    def _patch_exec(cls):
        if not hasattr(cls, "exec_"):
            cls.exec_ = cls.exec

    _patch_exec(QtWidgets.QDialog)
    _patch_exec(QtWidgets.QMenu)
    _patch_exec(QtWidgets.QApplication)

    # ── QShortcut / QAction: moved from QtWidgets → QtGui in PySide6 ─────
    if not hasattr(QtWidgets, "QShortcut"):
        QtWidgets.QShortcut = QtGui.QShortcut
    if not hasattr(QtWidgets, "QAction"):
        QtWidgets.QAction = QtGui.QAction

except ImportError:
    from PySide2 import QtWidgets, QtCore, QtGui       # FreeCAD < 1.0
    _PYSIDE_VERSION = 2


def qenum_int(value) -> int:
    """
    Safely converts a Qt enum value to int.

    PySide2: int(enum)          works directly.
    PySide6: scoped enums — int() fails, .value delivers the int.

    Usage (instead of int(QtWidgets.QDialogButtonBox.Ok | ...)):
        qenum_int(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
    """
    try:
        return int(value)
    except TypeError:
        return int(value.value)


__all__ = ["QtWidgets", "QtCore", "QtGui", "_PYSIDE_VERSION", "qenum_int"]
