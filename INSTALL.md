# Installation & Setup Guide

This guide explains how to get the **DI-PASSIONATE FreeCAD Workbench** running from scratch, including developer tooling.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Install FreeCAD](#2-install-freecad)
3. [Install Python Dependencies](#3-install-python-dependencies)
4. [Install the Workbench](#4-install-the-workbench)
5. [Verify the Installation](#5-verify-the-installation)
6. [Developer Setup (VS Code)](#6-developer-setup-vs-code) — including [running the tests](#64-running-the-test-suite)
7. [Remote Debugging with debugpy](#7-remote-debugging-with-debugpy)
8. [Troubleshooting](#8-troubleshooting)
9. [Updating the Workbench](#updating-the-workbench)

---

## 1. Prerequisites

| Requirement | Minimum version | Notes |
|---|---|---|
| **FreeCAD** | 1.1 | See section 2 |
| **Python** | 3.11 | Bundled with FreeCAD — no separate install needed |
| **gdstk** | any recent | Only external dependency; see section 3 |
| **Draft workbench** | bundled | Ships with FreeCAD; used by the Interactive Router for live preview and snapping |
| **git** | any recent | To clone the repository |
| **Internet access** | — | Only required for the Leadframe Online Library |

No separate Python installation is needed — FreeCAD ships with its own embedded Python 3.11
interpreter, and `gdstk` is the only package you have to add.

---

## 2. Install FreeCAD

Download the **FreeCAD 1.1.1** installer from the official website:

<https://www.freecad.org/downloads.php>

Run the installer and accept the default installation path:

- **Windows:** `C:\Program Files\FreeCAD 1.1\`
- **Linux:** Follow the AppImage or package manager instructions on the download page.

After installation, launch FreeCAD once to let it create the user configuration directory, then close it again.

---

## 3. Install Python Dependencies

The workbench requires **gdstk** for reading GDSII files. It must be installed into FreeCAD's own Python environment (not a system Python).

### Windows

Open a **Command Prompt** or **PowerShell** and run:

```powershell
"C:\Program Files\FreeCAD 1.1\bin\python.exe" -m pip install gdstk
```

> If pip is not available, bootstrap it first:
> ```powershell
> "C:\Program Files\FreeCAD 1.1\bin\python.exe" -m ensurepip --upgrade
> "C:\Program Files\FreeCAD 1.1\bin\python.exe" -m pip install --upgrade pip
> "C:\Program Files\FreeCAD 1.1\bin\python.exe" -m pip install gdstk
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
"C:\Program Files\FreeCAD 1.1\bin\python.exe" -c "import gdstk; print(gdstk.__version__)"
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

It's also possible for **Windows** to choose a path like e.g. `C:\Program Files\FreeCAD 1.1\Mod\DI-PASSIONATE\`

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
4. Several toolbars should appear, each with a short caption underneath:
   **Tech**, **Import**, **Render**, **Package**, **Bonding**, **Routing**, **DRC** and
   **Workbench**. Four further toolbars — *Wire Bonding Session*, *Trace Routing Session*,
   *Batch Route Session* and *Contact Point Pattern* — stay hidden until the corresponding
   session is running; that is intentional.
5. Check the **Report View** (`View → Panels → Report View`). On a healthy start you should see
   `Commands loaded successfully` and `Toolbars initialized`.
6. To confirm GDS import works, use **Load GDSII** and select the sample file at
   `resources/gds/ALL_LNA.gds` inside the workbench folder.

If the workbench does not appear, check the Report View for error messages — most problems are
caused by a missing `gdstk` install or an incorrect folder name.

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
        "C:/Program Files/FreeCAD 1.1/bin",
        "C:/Program Files/FreeCAD 1.1/bin/Lib/site-packages",
        "C:/Users/<YourName>/AppData/Roaming/Python/Python311/site-packages"
    ],
    "python.defaultInterpreterPath": "C:/Program Files/FreeCAD 1.1/bin/python.exe"
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

### 6.4 Running the test suite

The workbench ships a headless test suite that runs inside FreeCAD's own interpreter. It
deliberately does not use pytest: the tests need a live FreeCAD and OCCT, and FreeCAD's
bundled Python may not have pytest available.

**Windows**

```powershell
& "C:\Program Files\FreeCAD 1.1\bin\freecadcmd.exe" tests\run_all.py
```

**Linux**

```bash
freecadcmd tests/run_all.py
```

The runner prints a summary, writes the same report to `tests/results.log`, and exits
non-zero if any check fails — so it can be dropped straight into CI. Expect output ending in:

```
RESULTS: 703 passed, 0 failed, 703 total
All checks passed.
```

Each test module exposes a single `run()` function returning a list of results; add new
modules to the `MODULES` list in `tests/run_all.py`. Algorithmic code lives in `core/` and is
Qt-free precisely so it can be covered here — GUI command files cannot be, since `freecadcmd`
has no GUI.

---

## 7. Remote Debugging with debugpy

You can attach the VS Code debugger to a running FreeCAD instance.

### Step 1 — Launch FreeCAD with the debug flag

**Windows PowerShell:**

```powershell
$env:FREECAD_DEBUGPY = "1"
& "C:\Program Files\FreeCAD 1.1\bin\FreeCAD.exe"
```

**Windows Command Prompt:**

```cmd
set FREECAD_DEBUGPY=1
"C:\Program Files\FreeCAD 1.1\bin\FreeCAD.exe"
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

### Interactive Route says "Select the face to route on"

The router needs to know which surface to work on. Select a face in the 3-D view — any face
of any body, flat or curved — before starting the tool. Selecting a whole object instead
falls back to its uppermost horizontal face.

### Interactive Route reports "No way through to the cursor"

The head is blocked by copper at the current clearance. This is normal feedback, not an
error: move the cursor, press `/` to flip the corner, or reduce the clearance. The router
deliberately refuses to draw a trace that would violate clearance.

### Batch Auto-Route reports "could not be routed and were skipped"

Some pairs genuinely cannot be routed on a single layer at the requested clearance.
Things to try, in order: enable **Reroute existing traces when a pair is blocked** (on by
default) so the router may move one existing trace aside; reduce the clearance or the
trace spacing; queue the hardest pairs *first*, since earlier pairs get the free space;
or route the remaining connection by hand with **Interactive Route**. The pairs are always
listed by name — nothing is dropped silently.

### "Center on face" or Contact Point Symmetry says no face is selected

Both need an actual **face** — click the face itself in the 3-D view, not an edge, a vertex
or the whole object in the tree. They refuse rather than falling back to a different
reference, because a silently different answer is worse than a clear refusal. Ctrl+click to
add more faces; both tools accept several at once. In either dialog, remember to press
*↺ Read current FreeCAD selection* after changing the selection.

### Contact Point Pattern will not start

It needs at least one **face** selected — Ctrl+click the faces to place points on.
Selecting an existing contact point as well is optional: it copies that point's position
onto the other faces, and its own face is a valid target too (select it to extend that
point into a row on the same face).

### Mirrored contact points were "skipped"

Points whose mirror image falls outside the face are reported and skipped rather than
created off the part. This happens when the pattern is not symmetric with respect to the
face's own outline — for example points near one edge of a face that is not itself
symmetric about the mirror axis.

### View in GDS3D asks for an executable

GDS3D is a separate program and is **not bundled** — it is GPL-2 (because of the Gmsh code
it contains), which cannot be combined with this workbench's GPL-3-or-later into one work.
Launching it as its own process is fine, so you install it yourself:

- **Prebuilt (all platforms):** <https://sourceforge.net/projects/gds3d/files/> →
  `GDS3D_1.8.zip`
- **From source:** <https://github.com/trilomix/GDS3D> — Windows: open `win32/GDS3D.sln` in
  Visual Studio and build. Linux: `make -C linux` (needs G++, GL, GLU, GLX and X11 dev
  packages). macOS: `mac/GDS3D.xcodeproj` in Xcode.

The command asks for the executable once and remembers it. Note the SourceForge 1.8 build
is the original University of Twente release; the Gmsh export (`F` key) and the
`Material` / `MinSpace` fields are documented in the active forks, so build a fork if the
export is missing.

### 3-D Route says the pads are not on one body

It connects two pads on **different faces of the same body**, following the surface across
the shared edges. Pads on two separate objects are refused deliberately — there is no
surface between them to route along. If a route across one body still fails, raise
**Max faces crossed**, or increase the clearance.

### Trace routing finds no path on a dense board

The grid-based **Trace Routing** tool searches between two fixed points and can legitimately
fail where a board is congested. Reduce the clearance, loosen the maximum bend angle, or use
the **Interactive Route** tool instead and steer around the obstruction yourself.

### Online library shows no packages / loads slowly

The library fetches data from the MirrorSemi website. Check your internet connection. A corporate proxy or firewall may block the requests.

### `debugpy` not found

Install it into FreeCAD's Python:

```powershell
"C:\Program Files\FreeCAD 1.1\bin\python.exe" -m pip install debugpy
```

### FreeCAD crashes on startup after installing the workbench

A syntax error in a workbench file can crash the FreeCAD Python loader. Check the FreeCAD log file:

- **Windows:** `%APPDATA%\FreeCAD\FreeCAD.log`
- **Linux:** `~/.local/share/FreeCAD/FreeCAD.log`

---

## Updating the Workbench

If you installed via git, pull the latest changes.

**Windows (PowerShell):**

```powershell
cd "$env:APPDATA\FreeCAD\Mod\DI-PASSIONATE-FreeCAD"
git pull
```

**Linux / macOS:**

```bash
cd ~/.local/share/FreeCAD/Mod/DI-PASSIONATE-FreeCAD
git pull
```

Restart FreeCAD after updating. If an update adds a new dependency, re-run the pip command
from [section 3](#3-install-python-dependencies).
