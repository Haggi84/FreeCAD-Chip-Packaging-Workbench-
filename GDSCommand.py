from PySide2 import QtWidgets
import mymodule  # stellt sicher, dass dein Modul geladen wird

class GDSCommand:
    def GetResources(self):
        return {
            'MenuText': 'load GDSII',
            'ToolTip': 'load GDSII file with technology reference and show layers',
            'Pixmap': ''  # Optional: Icon Pfad hier
        }

    def Activated(self):
        try:
            gds_path, _ = QtWidgets.QFileDialog.getOpenFileName(None, "GDSII Datei wählen", "", "GDSII Dateien (*.gds)")
            lyp_path, _ = QtWidgets.QFileDialog.getOpenFileName(None, "lyp Datei wählen", "", ".lyp Dateien (*.lyp)")
            if not gds_path:
                print("no file selected.")
                return
                
            if not lyp_path:
                print("no file selected.")
                return
                
            layers = mymodule.load_gdsii(gds_path)
            
            if not layers:
                print("didn't find any layer")
                return
            self.widget = mymodule.run(gds_path, lyp_path)
            #layer, ok = QtWidgets.QInputDialog.getItem(None, "select layer", "Layer:", [str(l) for l in layers], 0, False)
            #if ok:
                #mymodule.display_layer(gds_path, int(layer))
        except Exception as e:
            print("error during execution:", e)

    def IsActive(self):
        return True

import FreeCADGui
FreeCADGui.addCommand('GDSCommand', GDSCommand())
