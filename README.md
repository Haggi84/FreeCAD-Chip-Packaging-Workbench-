# DI-PASSIONATE-FreeCAD
## Development of a FreeCAD plugin for the BMBF project DI-Passionate to enable chip-packaging

This is a Python AddIn for the OpenSource software FreeCAD (https://www.freecad.org/downloads.php). It aims to provide a opportunity to import GDSII files for chipdesign. 

Following features are currently planned:

- Importing a full visualization for GDSII files. E.g. directly generated out of the software KLayout (https://www.klayout.de/)
- An editor and/or a library for importing leadframe structures for chips
- functionality to place chips and chiplets inside an housing
- plan bonding wires
- assign materials
- export of assemblies for e.g. thermal simulations
    
The imported file is displayed in a workbench environment. Currently we are at the beginning of the development. 

A possible result for the software could look like some configuration module that allows to configure the elements as stated in the picture below:

<img width="511" height="334" alt="grafik" src="https://github.com/user-attachments/assets/5ac820ee-de2e-4051-97c5-c6499160bba8" />


## Setup

To run the AddIn just clone the repository via the command:

```git clone %PATHTOREPOSITORY%``` in the ```../FreeCAD/Mod/``` folder

It is suggested to use VSCode. You can download it from the following page:

https://code.visualstudio.com/

Once you cloned the repository in the ```../FreeCAD/Mod/``` folder, you have to set up the project by opening the project and add a new folder ```.vscode``` here:

<img width="300" height="115" alt="grafik" src="https://github.com/user-attachments/assets/4ddb10b0-dc88-4461-9dc9-37e0b32c6d1f" />

create a .json file with the name:

**settings.json** 

Insert this code (for Windows systems; you might adjust if you're using a Linux based system) and save the file.

```
{
    "python.analysis.extraPaths": [
        "C:/Program Files/FreeCAD 1.0/bin",
        "C:/users/%USER%/appdata/roaming/python/python311/site-packages",
        "C:/Program Files/FreeCAD 1.0/bin/Lib/site-packages"
    ]
}
```

_**note:** you have to replace ```%USER%``` with the corresponding directory of your system_
