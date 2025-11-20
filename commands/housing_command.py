# Auto-generated wrapper commands to ensure registration and expected class names.

import FreeCAD, FreeCADGui
try:
    from dialogs.housing_config import TransparentHousingConfigurator
except Exception:
    TransparentHousingConfigurator = None
try:
    from core.housing import build_housing
except Exception:
    build_housing = None

class HousingCommand:
    def GetResources(self):
        return {'MenuText':'Create Housing','ToolTip':'Gehäuse erzeugen','Pixmap':''}
    def IsActive(self):
        return True
    def Activated(self):
        try:
            config = {}
            if TransparentHousingConfigurator:
                try:
                    dlg = TransparentHousingConfigurator()
                    if hasattr(dlg, 'exec_') and dlg.exec_():
                        config = getattr(dlg, 'config', {}) or {}
                except Exception:
                    pass
            if not config:
                config = {
                    "frame_type": "QFN (Quad Flat No-lead)",
                    "frame_length": 10.0, "frame_width": 10.0,
                    "frame_thickness": 1.0,
                    "wall_thickness": 0.8, "clearance": 0.2,
                    "housing_height": 5.0,
                    "include_lid": True, "lid_thickness": 0.8,
                    "material": "PC", "transparency": 0.6,
                    "lead_length": 0.8, "qfn_pad_thickness": 0.1, "bga_ball_diameter": 0.4
                }
            if build_housing:
                build_housing(config)
            else:
                FreeCAD.Console.PrintMessage('HousingCommand activated (wrapper).\n')
        except Exception as e:
            FreeCAD.Console.PrintError(f'HousingCommand failed: {e}\n')

FreeCADGui.addCommand('HousingCommand', HousingCommand())
