# Auto-generated wrapper commands to ensure registration and expected class names.

import FreeCAD, FreeCADGui
try:
    from panels.property_panel import PropertyPanel
except Exception:
    PropertyPanel = None
try:
    from dialogs.layer_selector import LayerSelector
except Exception:
    LayerSelector = None
try:
    from core import mymodule
except Exception:
    mymodule = None

class GDSCommand:
    def GetResources(self):
        return {'MenuText':'Import GDS','ToolTip':'GDS-Datei importieren','Pixmap':''}
    def IsActive(self):
        return True
    def Activated(self):
        try:
            doc = FreeCAD.ActiveDocument or FreeCAD.newDocument('GDS')
            # If a legacy activate() exists, call it
            try:
                # legacy function hook
                import GDSCommand as legacy  # top-level legacy file (if present)
                if hasattr(legacy, 'run') and callable(legacy.run):
                    return legacy.run()
            except Exception:
                pass
            # Fallback: just show PropertyPanel if available
            if PropertyPanel:
                FreeCADGui.Control.showDialog(PropertyPanel(doc))
            else:
                FreeCAD.Console.PrintMessage('GDSCommand activated (wrapper).\n')
        except Exception as e:
            FreeCAD.Console.PrintError(f'GDSCommand failed: {e}\n')

# Register
FreeCADGui.addCommand('GDSCommand', GDSCommand())
