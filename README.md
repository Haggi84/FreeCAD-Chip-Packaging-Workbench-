# DI-PASSIONATE-FreeCAD
## Development of a FreeCAD plugin for the BMBF project DI-Passionate to enable chip-packaging

This is a Python AddIn for the OpenSource software FreeCAD. It aims to provide a opportunity to import GDSII files for chipdesign. The imported file is displayed in a workbench environment. Currently we are at the beginning of the development. 

To run the AddIn just clone the repository via the command:

```git clone %PATHTOREPOSITORY%``` in the ```../FreeCAD/Mod/``` folder

It is suggested to use VSCode. You can download it from the following page:

https://code.visualstudio.com/

Once you cloned the repository in the ```../FreeCAD/Mod/``` folder, you have to set up the project by opening the project and add a new folder ```.vscode``` here:

<img width="300" height="115" alt="grafik" src="https://github.com/user-attachments/assets/4ddb10b0-dc88-4461-9dc9-37e0b32c6d1f" />

create a .json file with the name:

**settings.json** 
```
{
    "python.analysis.extraPaths": [
        "C:/Program Files/FreeCAD 1.0/bin",
        "C:/users/jozeitler/appdata/roaming/python/python311/site-packages",
        "C:/Program Files/FreeCAD 1.0/bin/Lib/site-packages"
    ]
}
```
