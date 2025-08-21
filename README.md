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

Here are some general ideas (Mindmap):

https://lucid.app/lucidspark/ebb96ac9-c6d3-408a-9ead-51c1aa83efa1/edit?invitationId=inv_3ef9b6cf-fcc6-4717-8b34-9a1598ceaaf7

The imported file is displayed in a workbench environment. Currently we are at the beginning of the development. 

A possible result for the software could look like some configuration module that allows to configure the elements as stated in the picture below:

<img width="511" height="334" alt="grafik" src="https://github.com/user-attachments/assets/5ac820ee-de2e-4051-97c5-c6499160bba8" />


## Setup

To run the AddIn just clone the repository via the command:

```git clone %PATHTOREPOSITORY%``` in the ```../FreeCAD/Mod/``` folder

## Developer settings

It is suggested to use VSCode. You can download it from the following page:

https://code.visualstudio.com/

Once you cloned the repository in the ```../FreeCAD/Mod/``` folder, you have to set up the project:

1. Open the project in VSCode.
2. Create a folder named ```.vscode``` in the project root.

   <img width="300" height="115" alt="grafik" src="https://github.com/user-attachments/assets/4ddb10b0-dc88-4461-9dc9-37e0b32c6d1f" />

3. Inside ```.vscode```, create a file named **settings.json**.
4. Add the configuration appropriate for your system and save the file.

**Windows example**

```json
{
    "python.analysis.extraPaths": [
        "C:/Program Files/FreeCAD 1.0/bin",
        "C:/Users/%USER%/AppData/Roaming/Python/Python311/site-packages",
        "C:/Program Files/FreeCAD 1.0/bin/Lib/site-packages"
    ]
}
```

Replace `%USER%` with your Windows user name and change `1.0` to the FreeCAD version you installed (e.g., `0.21`).

**Linux example**

```json
{
    "python.analysis.extraPaths": [
        "/usr/lib/freecad/bin",
        "/home/<username>/.local/lib/python3.11/site-packages",
        "/usr/lib/freecad/lib/site-packages"
    ]
}
```

Replace `<username>` with your Linux user name and adjust the FreeCAD paths and version numbers to match your installation.



