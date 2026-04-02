# Installation & Setup Guide

This guide explains how to get the **DI-PASSIONATE FreeCAD Workbench** running from scratch, including developer tooling.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Install FreeCAD](#2-install-freecad)
3. [Install Python Dependencies](#3-install-python-dependencies)
4. [Install the Workbench](#4-install-the-workbench)
5. [Verify the Installation](#5-verify-the-installation)
6. [Developer Setup (VS Code)](#6-developer-setup-vs-code)
7. [Remote Debugging with debugpy](#7-remote-debugging-with-debugpy)
8. [Troubleshooting](#8-troubleshooting)

---

## 1. Prerequisites

| Requirement | Minimum version | Notes |
|---|---|---|
| **FreeCAD** | 1.0 | See section 2 |
| **Python** | 3.11 | Bundled with FreeCAD — no separate install needed |
| **git** | any recent | To clone the repository |
| **Internet access** | — | Required for the Leadframe Online Library |

No separate Python installation is needed — FreeCAD ships with its own embedded Python 3.11 interpreter.

---

## 2. Install FreeCAD

Download the **FreeCAD 1.0** installer from the official website:

<https://www.freecad.org/downloads.php>

Run the installer and accept the default installation path:

- **Windows:** `C:\Program Files\FreeCAD 1.0\`
- **Linux:** Follow the AppImage or package manager instructions on the download page.

After installation, launch FreeCAD once to let it create the user configuration directory, then close it again.

---

## 3. Install Python Dependencies

The workbench requires **gdstk** for reading GDSII files. It must be installed into FreeCAD's own Python environment (not a system Python).

### Windows

Open a **Command Prompt** or **PowerShell** and run:

```powershell
"C:\Program Files\FreeCAD 1.0\bin\python.exe" -m pip install gdstk
```

> If pip is not available, bootstrap it first:
> ```powershell
> "C:\Program Files\FreeCAD 1.0\bin\python.exe" -m ensurepip --upgrade
> "C:\Program Files\FreeCAD 1.0\bin\python.exe" -m pip install --upgrade pip
> "C:\Program Files\FreeCAD 1.0\bin\python.exe" -m pip install gdstk
> ```

### Linux

```bash
# Find the FreeCAD Python binary (path depends on your install method)
freecad-python3 -m pip install gdstk

# If using an AppImage, extract or mount it to locate the bundled Python:
# /path/to/FreeCAD.AppImage --appimage-mount
# Then run the extracted python binary with -m pip install gdstk
```

### Verify the install

```powershell
"C:\Program Files\FreeCAD 1.0\bin\python.exe" -c "import gdstk; print(gdstk.__version__)"
```

You should see a version number printed (e.g. `0.9.x`).

---

## 4. Install the Workbench

The workbench must be placed inside FreeCAD's **user `Mod` folder** — not the system installation folder — so it survives FreeCAD updates.

### Locate the user Mod folder

| Platform | Path |
|---|---|
| **Windows** | `%APPDATA%\FreeCAD\Mod\` → typically `C:\Users\<YourName>\AppData\Roaming\FreeCAD\Mod\` |
| **Linux** | `~/.local/share/FreeCAD/Mod/` |
| **macOS** | `~/Library/Application Support/FreeCAD/Mod/` |

If the `Mod` folder does not exist yet, create it.

### Clone the repository

**Windows (Command Prompt):**

```cmd
git clone <repository-url> "%APPDATA%\FreeCAD\Mod\DI-PASSIONATE-FreeCAD"
```

**Windows (PowerShell):**

```powershell
git clone <repository-url> "$env:APPDATA\FreeCAD\Mod\DI-PASSIONATE-FreeCAD"
```

**Linux / macOS:**

```bash
git clone <repository-url> ~/.local/share/FreeCAD/Mod/DI-PASSIONATE-FreeCAD
```

> Replace `<repository-url>` with the actual URL of this repository.

### Alternative: manual copy

If you downloaded a ZIP archive instead of using git, extract it so that the folder structure is:

```
%APPDATA%\FreeCAD\Mod\DI-PASSIONATE-FreeCAD\
    InitGui.py
    version.py
    core\
    gds\
    leadframe\
    ...
```

---

## 5. Verify the Installation

1. Start FreeCAD.
2. Open the **Workbench selector** (the drop-down at the top of the screen that shows the active workbench name).
3. Select **Chip-Packaging Workbench** from the list.
4. A toolbar labelled **GDSII Tools** should appear with icons for all tools.
5. To confirm the GDS import works, use **Load GDSII** and select the sample file at `resources/gds/ALL_LNA.gds` inside the workbench folder.

If the workbench does not appear, check the FreeCAD **Report View** panel (`View → Panels → Report View`) for error messages — most problems are caused by a missing `gdstk` install or an incorrect folder name.

---

## 6. Developer Setup (VS Code)

### 6.1 Open the project

Open the workbench folder as a VS Code workspace:

```
File → Open Folder → %APPDATA%\FreeCAD\Mod\DI-PASSIONATE-FreeCAD
```

### 6.2 Configure Python path for IntelliSense

Create `.vscode/settings.json` inside the project folder so that the VS Code Python extension can resolve FreeCAD modules:

**Windows**

```json
{
    "python.analysis.extraPaths": [
        "C:/Program Files/FreeCAD 1.0/bin",
        "C:/Program Files/FreeCAD 1.0/bin/Lib/site-packages",
        "C:/Users/<YourName>/AppData/Roaming/Python/Python311/site-packages"
    ],
    "python.defaultInterpreterPath": "C:/Program Files/FreeCAD 1.0/bin/python.exe"
}
```

**Linux**

```json
{
    "python.analysis.extraPaths": [
        "/usr/lib/freecad/lib",
        "/usr/lib/freecad-python3/lib",
        "/home/<YourName>/.local/lib/python3.11/site-packages"
    ]
}
```

> Replace `<YourName>` with your actual Windows username.

### 6.3 Recommended VS Code extensions

| Extension | Purpose |
|---|---|
| **Python** (Microsoft) | Linting, IntelliSense, debugging |
| **Pylance** | Fast type checking for the FreeCAD stubs |
| **GitLens** | Enhanced git history view |

---

## 7. Remote Debugging with debugpy

You can attach the VS Code debugger to a running FreeCAD instance.

### Step 1 — Launch FreeCAD with the debug flag

**Windows PowerShell:**

```powershell
$env:FREECAD_DEBUGPY = "1"
& "C:\Program Files\FreeCAD 1.0\bin\FreeCAD.exe"
```

**Windows Command Prompt:**

```cmd
set FREECAD_DEBUGPY=1
"C:\Program Files\FreeCAD 1.0\bin\FreeCAD.exe"
```

**Linux / macOS:**

```bash
FREECAD_DEBUGPY=1 freecad
```

FreeCAD will pause on startup and print:

```
debugpy: waiting for VS Code attach on port 5678...
```

### Step 2 — Attach VS Code

Add the following configuration to `.vscode/launch.json` (create the file if it does not exist):

```json
{
    "version": "0.2.0",
    "configurations": [
        {
            "name": "Attach to FreeCAD",
            "type": "python",
            "request": "attach",
            "connect": {
                "host": "localhost",
                "port": 5678
            }
        }
    ]
}
```

Press **F5** (or **Run → Start Debugging**) with the "Attach to FreeCAD" configuration selected. FreeCAD will resume once VS Code is attached.

### Step 3 — Set breakpoints

Set breakpoints anywhere in the workbench Python files. They will be hit the next time the corresponding FreeCAD command is executed.

---

## 8. Troubleshooting

### Workbench does not appear in the selector

- Check that the folder is named exactly `DI-PASSIONATE-FreeCAD` (case-sensitive on Linux).
- Check that `InitGui.py` exists directly inside that folder.
- Open **View → Panels → Report View** in FreeCAD and look for Python error messages.

### `ModuleNotFoundError: No module named 'gdstk'`

Run the pip install command from section 3, making sure to use **FreeCAD's own Python binary**, not a system Python.

### Toolbar appears but all icons are greyed out

Some commands require an open FreeCAD document. Use **File → New** to create a document, then activate the workbench.

### Load GDSII fails with "No layers found"

- Confirm that your `.lyp` file was exported from the same KLayout version/technology as the `.gds` file.
- The layer IDs in the `.lyp` must match those in the `.gds`. Open the `.gds` in KLayout and compare.

### Online library shows no packages / loads slowly

The library fetches data from the MirrorSemi website. Check your internet connection. A corporate proxy or firewall may block the requests.

### `debugpy` not found

Install it into FreeCAD's Python:

```powershell
"C:\Program Files\FreeCAD 1.0\bin\python.exe" -m pip install debugpy
```

### FreeCAD crashes on startup after installing the workbench

A syntax error in a workbench file can crash the FreeCAD Python loader. Check the FreeCAD log file:

- **Windows:** `%APPDATA%\FreeCAD\FreeCAD.log`
- **Linux:** `~/.local/share/FreeCAD/FreeCAD.log`

---

## Updating the Workbench

If you installed via git, pull the latest changes:

```bash
cd "%APPDATA%\FreeCAD\Mod\DI-PASSIONATE-FreeCAD"
git pull
```

Restart FreeCAD after updating.
