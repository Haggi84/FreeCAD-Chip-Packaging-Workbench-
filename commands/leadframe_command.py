# Auto-generated wrapper commands to ensure registration and expected class names.

import FreeCAD, FreeCADGui
try:
    from dialogs.leadframe_config import LeadframeConfigurator
except Exception:
    LeadframeConfigurator = None
try:
    from core.leadframe import build_leadframe
except Exception:
    build_leadframe = None

class LeadframeCommand:
    def GetResources(self):
        return {'MenuText':'Create Leadframe','ToolTip':'Leadframe erzeugen','Pixmap':''}
    def IsActive(self):
        return True
    def Activated(self):
        try:
            config = {}
            if LeadframeConfigurator:
                try:
                    dlg = LeadframeConfigurator()
                    if hasattr(dlg, 'exec_') and dlg.exec_():
                        config = getattr(dlg, 'config', {}) or {}
                except Exception:
                    pass
            if not config:
                config = {
                    "frame_type": "QFN (Quad Flat No-lead)",
                    "frame_length": 10.0,
                    "frame_width": 10.0,
                    "frame_thickness": 1.0,
                    "material": "Cu",
                    "left_lead_count": 0, "right_lead_count": 0,
                    "top_lead_count": 0, "bottom_lead_count": 0,
                    "lead_width": 0.3, "lead_pitch": 0.5, "lead_length": 0.8,
                    "qfn_pad_thickness": 0.1,
                    "bga_ball_diameter": 0.4, "bga_ball_pitch": 0.8
                }
            if build_leadframe:
                doc = FreeCAD.ActiveDocument or FreeCAD.newDocument('Leadframe')
                build_leadframe(config, doc=doc)
            else:
                FreeCAD.Console.PrintMessage('LeadframeCommand activated (wrapper).\n')
        except Exception as e:
            FreeCAD.Console.PrintError(f'LeadframeCommand failed: {e}\n')

FreeCADGui.addCommand('LeadframeCommand', LeadframeCommand())
