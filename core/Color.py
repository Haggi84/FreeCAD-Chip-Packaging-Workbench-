from compat import QtGui

# -----------------------
# Color / style helpers
# -----------------------
def hex_to_rgb(hex_color):
    """Convert a hex color string to an RGB tuple in 0..1."""
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def hex_to_qcolor(hex_color):
    """Convert a hex color string to a QColor."""
    return QtGui.QColor(hex_color)