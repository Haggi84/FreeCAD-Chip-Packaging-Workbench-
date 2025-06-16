import FreeCADGui
import mymodule 

class MyCommand:
    def GetResources(self):
        return {'MenuText': 'create cube',
                'ToolTip': 'creates a 20x20x20 cube',
                'Pixmap': ''}

    def Activated(self):
        mymodule.run()

    def IsActive(self):
        return True

FreeCADGui.addCommand('MyCommand', MyCommand())
