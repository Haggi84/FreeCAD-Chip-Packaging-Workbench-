# Auto-generated wrapper commands to ensure registration and expected class names.

import FreeCAD, FreeCADGui
try:
    from dialogs.layeronleadframe_config import LayeronLeadframeConfigurator
except Exception:
    LayeronLeadframeConfigurator = None
try:
    from dialogs.layer_selector import LayerSelector
except Exception:
    LayerSelector = None
try:
    from core.layer_stack import finalize_import
    from core import mymodule
    from core.color import hex_to_rgb
except Exception:
    finalize_import = None
    mymodule = None
    def hex_to_rgb(x): return (1.0,1.0,1.0)

class LayeronLeadframe:
    def GetResources(self):
        return {'MenuText':'Layer on Leadframe','ToolTip':'Layer (GDS) platzieren','Pixmap':''}
    def IsActive(self):
        return True
    def Activated(self):
        try:
            doc = FreeCAD.ActiveDocument or FreeCAD.newDocument('LayerOnLeadframe')
            # placeholder — user can insert full flow here; we just avoid command-not-found
            FreeCAD.Console.PrintMessage('LayeronLeadframe activated (wrapper).\n')
        except Exception as e:
            FreeCAD.Console.PrintError(f'LayeronLeadframe failed: {e}\n')

FreeCADGui.addCommand('LayeronLeadframe', LayeronLeadframe())
