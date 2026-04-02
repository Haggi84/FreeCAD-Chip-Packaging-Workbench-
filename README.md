# DI-PASSIONATE-FreeCAD

![Version](https://img.shields.io/badge/version-0.5.0-green?style=flat-square)
![FreeCAD](https://img.shields.io/badge/FreeCAD-1.0-blue?style=flat-square)
![Python](https://img.shields.io/badge/Python-3.11-yellow?style=flat-square)
![Semantic Versioning](https://img.shields.io/badge/semver-2.0.0-informational?style=flat-square)

**A FreeCAD workbench for chip-packaging workflows, developed as part of the BMBF project DI-Passionate.**

This Python AddIn for [FreeCAD](https://www.freecad.org/downloads.php) provides a dedicated **Chip-Packaging Workbench** covering the full chip-assembly design flow: importing GDSII chip layouts, designing leadframes and housings, placing chips, planning bond wires, managing contact points, and saving/restoring sessions.

> For detailed setup instructions see **[INSTALL.md](INSTALL.md)**.

---

## Features

### Implemented

| Tool | Description |
|---|---|
| **Load GDSII** | Import `.gds` files with layer colours from a KLayout `.lyp` file and optional IHP `.map` technology file |
| **Leadframe Configurator** | Parametrically generate QFN, QFP, or BGA leadframes with configurable body size, lead count, and material |
| **Center Leadframe on GDS** | Auto-align the leadframe centre to the bounding box of the imported GDS geometry |
| **Leadframe Online Library** | Browse, preview real product photos, and download STEP/IGES/DXF packages from the MirrorSemi CAD catalogue |
| **Layer on Leadframe** | Scale and place selected GDS layers on an existing leadframe with rotation and mirror options |
| **Housing Configurator** | Generate a transparent IC housing/mold-compound body around the leadframe |
| **Define Contact Points** | Place bondable contact-point markers on selected GDS layer objects |
| **Set Contact Points on Face** | Interactive: select a package body, highlight its top surface in yellow, click to place contact points at exact positions |
| **Contact Point Browser** | Dock panel listing and highlighting all contact points in the 3D view |
| **Manual Wire Bonding** | Interactive session — click a die-pad contact point, then a leadframe contact point; a 3D bond wire is created |
| **Cancel Wire Bonding** | Exit an active wire-bonding session without saving |
| **Save Session** | Persist all design actions and parameters to a `.dipas` JSON file |
| **Load Session** | Restore a previous design session from a `.dipas` file |
| **Help Guide** | In-app help dialog with Overview, Quick Start, Tools, Workflows, and Troubleshooting tabs |
| **About** | Version and project information dialog |

### Planned / In Progress

- Export of assemblies for thermal simulations
- Expanded material assignment system
- Online library integration beyond MirrorSemi

---

## Workflow Overview

![Chip-packaging workflow diagram](resources/workflow.svg)

---

## Typical Workflow

| Step | Tool | Details |
|------|------|---------|
| **1** | **Load GDSII** | Select `.gds` + `.lyp` (+ optional `.map`), choose layers, optionally enable Auto PIN Detection |
| **2** | **Leadframe Configurator** or **Online Library** | Generate QFN / QFP / BGA leadframe, then run **Center Leadframe on GDS** |
| **3** | **Layer on Leadframe** | Scale and rotate the GDS chip onto the die paddle |
| **4** | **Housing Configurator** | Generate transparent mold compound body |
| **5** | **Define Contact Points** or **Set Contact Points on Face** | Manual contact point placement — skip if Auto PIN Detection was used in step 1 |
| **6** | **Manual Wire Bonding** | Click die pad → click leadframe lead → 3D bond wire created; repeat per bond |
| **7** | **Save Session** | Save all parameters to a `.dipas` file so the session can be resumed later |
| **8** | **Export** *(planned)* | Assembly export for thermal simulation |

---

## GDS Import Options

When loading a GDSII file the **Layer Selector** dialog exposes several import options:

| Option | Effect |
|---|---|
| **Match KLayout colours** | Apply exact fill/frame colours from the `.lyp` file |
| **Highlight bondable pads** | Render top-metal / PIN layers in gold |
| **3D extrusion** | Extrude each layer to its real Z-height using the `.map` stack definition |
| **Auto PIN contact detection** | Automatically create `ContactPoint` markers on the top PIN layers |

Filler layers (marked `FILL` in the `.map` file) are represented as a single bounding-box solid to keep import performance high. A progress dialog with a **Cancel** button is shown during import.

---

## File Inputs

| File | Purpose |
|---|---|
| `.gds` / `.GDS` | GDSII layout (output of KLayout, Cadence, etc.) |
| `.lyp` | KLayout layer properties — defines layer colours and visibility |
| `.map` | IHP technology map — layer names, EDI types, Z-stack heights |

The IHP Open PDK (including sample `.map` files) is available at:
<https://github.com/IHP-GmbH/IHP-Open-PDK>

A sample `.gds` file for testing is included at `resources/gds/ALL_LNA.gds`.

---

## Session Files (`.dipas`)

Each time you run **Save Session** the workbench writes a `.dipas` JSON file containing:

- All design actions in order (GDS import paths, leadframe config, housing config, wire-bond config, …)
- Timestamps and the associated FreeCAD document path

Reopen a session with **Load Session** to restore parameters and re-apply them.

---

## Project Structure

```
DI-PASSIONATE-FreeCAD/
├── InitGui.py                  # Workbench registration & toolbar definition
├── version.py                  # Single source of truth for the version number
├── Get_Path.py                 # Path helpers (icons, HTML resources)
├── core/
│   ├── Core_Functionality.py   # GDS parsing, shape building, layer styling
│   ├── leadframe.py            # Leadframe solid geometry builder
│   ├── housing.py              # Housing solid geometry builder
│   └── Color.py                # Colour utilities
├── gds/
│   ├── GDSCommand.py           # "Load GDSII" command + import pipeline
│   └── PropertyPanel.py        # Layer properties dock panel
├── leadframe/
│   ├── LeadframeCommand.py     # Leadframe Configurator + Center Leadframe commands
│   ├── LeadframeConfigurator.py# QFN / QFP / BGA configuration dialog
│   ├── LeadframeLibrary.py     # Online library browser (MirrorSemi)
│   └── LayeronLeadframe.py     # Layer-on-Leadframe command & dialog
├── housing/
│   ├── HousingCommand.py       # Housing Configurator command
│   └── HousingConfigurator.py  # Housing configuration dialog
├── wirebond/
│   ├── WirebondCommand.py      # Wire bonding commands (manual, cancel, browser)
│   ├── WirebondConfigurator.py # Wire bonding session configuration dialog
│   ├── ManualWireBonding.py    # Interactive bonding session logic
│   ├── ContactPointTool.py     # "Define Contact Points" command
│   ├── ContactPointPanel.py    # Contact Point Browser dock panel
│   ├── SetContactPointsOnFaceCommand.py  # Interactive top-face contact point placement
│   └── Wirebon_Confi_Support.py# Prerequisite checks
├── session/
│   ├── SessionManager.py       # Session record/persist/restore logic (.dipas)
│   ├── SaveSessionCommand.py   # "Save Session" toolbar command
│   └── LoadSessionCommand.py   # "Load Session" toolbar command
├── ui/
│   ├── LayerSelector.py        # Layer selection dialog (used during GDS import)
│   ├── ExtendedPropertyPanel.py
│   └── LayeronLeadframeConfigurator.py
├── help/
│   ├── HelpGuideCommand.py     # In-app help guide (HTML tabs)
│   └── AboutCommand.py         # About dialog
└── resources/
    ├── gds/ALL_LNA.gds         # Sample GDS file for testing
    ├── icons/                  # SVG/PNG toolbar icons
    ├── html/                   # HTML content for the in-app help guide
    └── workflow.svg            # Workflow overview diagram
```

---

## Quick Setup

See **[INSTALL.md](INSTALL.md)** for the full step-by-step guide covering:

- Installing FreeCAD 1.0
- Installing the `gdstk` Python dependency
- Cloning the workbench into the correct `Mod` folder
- Developer setup (VS Code IntelliSense + `debugpy` remote debugging)
- Troubleshooting common problems

**Short version:**

```bash
# Clone into FreeCAD's user Mod folder (Windows)
git clone <repository-url> "%APPDATA%/FreeCAD/Mod/DI-PASSIONATE-FreeCAD"

# Install the gdstk dependency into FreeCAD's Python
"C:/Program Files/FreeCAD 1.0/bin/python.exe" -m pip install gdstk
```

Restart FreeCAD — the **Chip-Packaging Workbench** will appear in the workbench selector.

---

## Design Notes / Mindmap

<https://lucid.app/lucidspark/ebb96ac9-c6d3-408a-9ead-51c1aa83efa1/edit?invitationId=inv_3ef9b6cf-fcc6-4717-8b34-9a1598ceaaf7>

A possible target UI showing a configuration module for chip-packaging elements:

<img width="511" height="334" alt="target UI concept" src="https://github.com/user-attachments/assets/5ac820ee-de2e-4051-97c5-c6499160bba8" />

An example for a bonded Chip looks within a package looks like this:

<img width="230" height="300" alt="bonded chip 1" src="https://github.com/user-attachments/assets/49d8373b-136f-4cf7-80d6-4976a90abba1" /> <img width="230" height="300" alt="bonded chip 1" src="https://github.com/user-attachments/assets/59d32864-7f0b-452e-8055-ff854130013b" />

---

## Contributing / Feedback

Issues and pull requests are welcome. Please open an issue describing any bug or feature request before submitting a large PR.
