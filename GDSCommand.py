import FreeCADGui
from PySide2 import QtWidgets
import mymodule

class GDSCommand:
    def GetResources(self):
        return {
            'MenuText': 'Load GDSII 3D Fast',
            'ToolTip': 'Fast load GDSII with LYP names, 3D bodies and Compound for performance',
            'Pixmap': ''
        }

    def Activated(self):
        try:
            gds_path, _ = QtWidgets.QFileDialog.getOpenFileName(None, "Select GDSII File", "", "GDSII Files (*.gds *.gdsii)")
            if not gds_path:
                return

            lyp_path, _ = QtWidgets.QFileDialog.getOpenFileName(None, "Select LYP File", "", "LYP Files (*.lyp)")
            if not lyp_path:
                return

            self.widget = mymodule.run(gds_path, lyp_path)

        except Exception as e:
            print("Error:", e)

    def IsActive(self):
        return True

FreeCADGui.addCommand('GDSCommand', GDSCommand())

