## License
This project is licensed under the GNU General Public License v3.0 —
see [LICENSE](LICENSE) for details.

# DI-PASSIONATE-FreeCAD

![Version](https://img.shields.io/badge/version-0.5.0-green?style=flat-square)
![FreeCAD](https://img.shields.io/badge/FreeCAD-1.1-blue?style=flat-square)
![Python](https://img.shields.io/badge/Python-3.11-yellow?style=flat-square)
![Semantic Versioning](https://img.shields.io/badge/semver-2.0.0-informational?style=flat-square)

**A FreeCAD workbench for chip-packaging workflows, developed as part of the BMBF project DI-Passionate.**

This Python AddIn for [FreeCAD](https://www.freecad.org/downloads.php) provides a dedicated **Chip-Packaging Workbench** covering the full chip-assembly design flow: importing GDSII chip layouts, positioning the chip, designing leadframes and housings, placing chips, planning bond wires and bumps, managing contact points, and saving/restoring sessions.

> For detailed setup instructions see **[INSTALL.md](INSTALL.md)**.

---

## Features

### Main Toolbar (always visible)

| Tool | Description |
|---|---|
| **PCB Import** | Load a PCB as a STEP file and automatically detect copper pad faces as ContactPoints (grouped as type "PCB") |
| **Move / Rotate PCB** | Reposition and rotate an already-loaded PCB together with all its auto-detected pad ContactPoints |
| **Load GDSII** | Import `.gds` files with layer colours from a KLayout `.lyp` file, optional IHP `.map` technology file, and optional stackup `.xml` for accurate 3-D Z-heights. Supports LOD mode (contact layers first, routing layers lazy) |
| **Toggle Performance Mode** | Switch all GDS layer objects between shaded Detail mode and fast Wireframe mode for viewport responsiveness |
| **Detail Layer Control** | LOD dock panel: shows all GDS layers with load state (SOLID / LOADING / DETAIL); click a row to load a routing layer on demand or use Z-cursor mode to auto-promote layers as you scroll through the stack |
| **Layer Slider** | Prusa-Slicer-style vertical slider — step through GDS layers from bottom to top for a quick stack review |
| **Leadframe Library** | Browse and import STEP package models from the MirrorSemi online CAD catalogue directly into the active GDS document |
| **Move / Rotate Chip** | Modeless dialog to translate and rotate all GDS chip objects as a group using arrow buttons or keyboard shortcuts |
| **Set Contact Points on Face** | Grid-based interactive tool: Ctrl+click faces → generate UV grid → click grid points → confirm to create contact point markers |
| **Contact Point Browser** | Dock panel listing all contact points grouped by type (die-side / housing / PCB), with 3-D highlight on hover |
| **Wire Bond** | Interactive 3-D wire bonding session — click a die-pad contact point, then a housing contact point; a solid swept-pipe bond wire is created |
| **Wire Bump Configurator** | Place parametric bump shapes (Ball, Wedge, Stitch, Nail Head) at both endpoints of selected bond wires via a netlist browser |
| **Cancel Wire Bonding** | Exit an active wire-bonding session |
| **Session ▾** | Dropdown: **Save Design Session** / **Load Design Session** — persist and replay the full design history as a `.dipas` JSON file |
| **Advanced Tools ▾** | Dropdown giving access to the advanced tool set (see below) |
| **Help Guide** | Modern in-app help dialog with sidebar navigation (Overview, Quick Start, Tool Reference, Workflows, Troubleshooting) |
| **About** | Version and project information |

### Advanced Tools (dropdown menu)

| Tool | Description |
|---|---|
| **Leadframe Configurator** | Parametrically generate QFN, QFP, or BGA leadframes with configurable body size, lead count, and material |
| **Center Leadframe** | Auto-align the leadframe centre to the bounding box of the imported GDS geometry |
| **Housing Configurator** | Generate a transparent IC housing/mold-compound body around the leadframe |
| **Layer on Leadframe** | Scale, rotate, and place selected GDS layers on an existing leadframe |
| **Define Contact Points** | Batch-place contact point markers at the top-face centre of selected GDS layer objects |

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
| **1** | **Load GDSII** | Select `.gds` + `.lyp` (+ optional `.map` + `.xml`), choose layers, optionally enable Auto PIN Detection |
| **2** | **Leadframe Library** or **Advanced → Leadframe Configurator** | Import STEP package or generate parametric QFN/QFP/BGA leadframe |
| **3** | **Advanced → Center Leadframe** | Align die-attach paddle to GDS bounding box |
| **4** | **Move / Rotate Chip** | Fine-tune chip position and orientation using arrow buttons or keyboard shortcuts |
| **5** | **Advanced → Layer on Leadframe** *(optional)* | Stack GDS layers directly onto the leadframe |
| **6** | **Advanced → Housing Configurator** *(optional)* | Generate transparent mold compound body |
| **7** | **Set Contact Points on Face** | Place contact point markers on leadframe/housing leads (die-side markers auto-placed if Auto PIN Detection was used in step 1) |
| **8** | **Wire Bond** | Click die pad → click housing lead → 3-D solid bond wire created; repeat per bond |
| **9** | **Wire Bump Configurator** | Select bump shape, adjust parameters, pick connections from netlist, place bumps |
| **10** | **Session ▾ → Save** | Save all parameters to a `.dipas` file for replay |

---

## GDS Import Options

When loading a GDSII file the **Layer Selector** dialog exposes several import options:

| Option | Effect |
|---|---|
| **Match KLayout colours** | Apply exact fill/frame colours from the `.lyp` file |
| **Highlight bondable pads** | Render top-metal / PIN layers in gold |
| **3-D extrusion** | Extrude each layer to its real Z-height using the stackup XML or `.map` stack definition |
| **Contacts-only 3-D** | Full geometry for bond-pad layers only; all other layers collapse to a single bounding-box body solid (fast for large chips) |
| **Auto PIN contact detection** | Automatically create `ContactPoint` markers on the top PIN layers (uses DT=2 pin markers for accurate pad-centre placement) |

Filler layers (marked `FILL` in the `.map` file or datatype 22) are represented as a single bounding-box solid to keep import performance high. A progress dialog with a **Cancel** button and ETA is shown during import.

---

## Level-of-Detail (LOD) Import

When **LOD mode** is active (the default), the importer categorises every layer before loading anything and only fetches full geometry for the layers you need immediately:

| Category | Layers | Import behaviour |
|---|---|---|
| **contact** | PIN, COMP, top/bottom metal bond layers | Full 3-D extrusion — loaded immediately |
| **pin_flat** | Pure PIN-marker layers | 2-D flat polygons — loaded immediately |
| **fill** | FILL / dummy-metal layers | Bounding-box solid only — never tessellated |
| **routing** | All remaining metal, via, drawing layers | Transparent placeholder box at correct Z — **lazy** |

Routing layers start as semi-transparent placeholder boxes that occupy the correct Z-range in the stack. Use the **Detail Layer Control** panel to promote individual layers to full geometry at any time:

- **Z-cursor mode** — drag a vertical cursor; the layer at that height is promoted automatically.
- **Free mode** — toggle each layer row independently.
- **Load All** — promotes every pending layer in parallel background threads.

This keeps initial import fast (seconds instead of minutes for large chips) while preserving the ability to inspect any layer in full detail on demand.

---

## PCB Integration

Import a PCB sub-board alongside the chip for package-on-package or SiP assemblies:

1. **PCB Import** — select a `.step` / `.stp` file; the tool places it as a `PCB_Board` object and auto-detects copper pad faces as ContactPoints (`PCB_Pad_NNN`).
2. **Move / Rotate PCB** — adjust X / Y / Z position and Z-rotation after import; the PCB and all its pad ContactPoints move as a unit.
3. Wire-bond from die pads to PCB pads exactly as you would to leadframe housing pads — the ContactPoint Browser lists PCB pads in a dedicated **PCB** group.

Pad detection heuristics: horizontal face, area between 0.01 mm² and 150 mm², Z-position in the top 4 % of the board's Z extent.

---

## Chip Transform Tool

The **Move / Rotate Chip** dialog provides incremental control over the position and orientation of all GDS chip objects:

- **Scope:** GDS Chip Objects only / All Document Objects / Current Selection
- **Translation:** configurable step size (mm), XY arrow cross, ±Z buttons
- **Rotation:** configurable step size (°), ±Rx / ±Ry / ±Rz buttons rotating around the bounding-box centre
- **Restore Original:** reverts all moves made in the current dialog session to the state captured when the dialog was opened
- **Keyboard shortcuts** (when dialog is focused):

| Key | Action |
|---|---|
| ← / → | ±X translation |
| ↑ / ↓ | ±Y translation |
| PgUp / PgDn | ±Z translation |
| Shift + ← / → | ±Rz rotation |
| Shift + ↑ / ↓ | ±Rx rotation |
| Shift + PgUp / PgDn | ±Ry rotation |

---

## Wire Bump Configurator

After creating bond wires with the Wire Bond tool, the **Wire Bump Configurator** lets you place realistic bump geometry at every wire endpoint:

| Bump Shape | Parameters | Typical Use |
|---|---|---|
| **Ball Bond** | Ball radius, neck radius, height | Thermosonic Au bonding |
| **Wedge Bond** | Width, length, height | Ultrasonic Al bonding |
| **Stitch Bond** | Radius, height | Second bond in ball-wedge sequence |
| **Nail Head** | Top radius, base radius, height | Heavy wire bonds |

The dialog includes a **live cross-section preview** (QPainter rendering) and a **netlist browser** showing all `BondWire_*` objects with net name, start/end contact points, and wire length. Check the desired connections and click **Place Bumps** to place at both endpoints.

---

## Contact Point System

Contact points are small `Part::Vertex` markers that define the bonding locations for the Wire Bond tool.

| Type | Name pattern | Colour | Created by |
|---|---|---|---|
| Die-side | `ContactPoint_NNN` | Orange | Auto PIN detection or Define Contact Points |
| Housing / leadframe | `contact_point_housing_NNN` | Yellow | Set Contact Points on Face |

The **Contact Point Browser** dock panel lists all markers grouped by type. Hovering a row highlights the corresponding marker in the 3-D view.

---

## File Inputs

| File | Purpose |
|---|---|
| `.gds` / `.GDS` | GDSII layout (output of KLayout, Cadence, etc.) |
| `.lyp` | KLayout layer properties — defines layer colours and visibility |
| `.map` | IHP technology map — layer names, EDI types (PIN, NET, VIA, FILL, …) |
| `.xml` | KLayout stackup XML — accurate Zmin/Zmax per layer from the PDK |
| `.step` / `.stp` | Package STEP model from MirrorSemi online library |
| `.dipas` | DI-PASSIONATE session file (JSON) — records all design actions for replay |

The IHP Open PDK (including sample `.map` files) is available at:
<https://github.com/IHP-GmbH/IHP-Open-PDK>

A sample `.gds` file for testing is included at `resources/gds/ALL_LNA.gds`.

---

## Session Files (`.dipas`)

Each **Save Session** writes a `.dipas` JSON file containing all design actions in order (GDS import paths, leadframe config, housing config, wire-bond config, …) plus timestamps and the associated FreeCAD document path.

Reopen a session with **Session ▾ → Load Design Session** to restore parameters and re-apply them from scratch — useful for regenerating a design after modifying the source GDS file.

---

## Project Structure

```
DI-PASSIONATE-FreeCAD/
├── InitGui.py                  # Workbench registration, toolbar, Advanced/Session dropdowns
├── version.py                  # Single source of truth for the version number
├── Get_Path.py                 # Path helpers (icons, HTML resources)
├── core/
│   ├── Core_Functionality.py   # GDS parsing, shape building, layer styling, auto PIN detection
│   ├── lod_import.py           # LOD layer categorisation + import-parameter helpers
│   ├── leadframe.py            # Leadframe solid geometry builder
│   ├── housing.py              # Housing solid geometry builder
│   ├── Color.py                # Colour utilities
│   ├── gds_io/
│   │   ├── cache.py            # Disk-cache helpers (cache key, load/save BREP cache)
│   │   ├── inspect.py          # Cheap GDS inspection (layer list, polygon counts, scale)
│   │   └── extract.py          # Shim → Core_Functionality load_gds / PIN import helpers
│   ├── tech/
│   │   ├── parsers.py          # parse_lyp, parse_map, parse_stackup_xml
│   │   ├── stackup.py          # Stack rank, build_stack_mm, build_stack_mm_from_xml
│   │   └── layer_info.py       # is_bondable, identify_contact_layers, style_for_material
│   └── geometry/
│       ├── transform.py        # transform_point, vec_transform, arr_to_tuples
│       ├── polygon.py          # polygon_area_mm2, simplify_poly, iter_xy
│       └── mesh.py             # ear_clip_triangulate, polygon_to_mesh_facets
├── gds/
│   ├── GDSCommand.py           # "Load GDSII" command + LOD-aware import pipeline
│   ├── ChipTransformCommand.py # "Move / Rotate Chip" modeless dialog
│   ├── ShowDetailLayerPanelCommand.py  # Opens Detail Layer Control dock panel
│   ├── ShowLayerSliderCommand.py       # Opens Layer Slider dock panel
│   ├── TogglePerformanceModeCommand.py # Toggles Detail ↔ Wireframe viewport mode
│   └── PropertyPanel.py        # Layer properties dock panel
├── pcb/
│   ├── PCBImportCommand.py     # "PCB Import" — load STEP, auto-detect pad ContactPoints
│   └── PCBPlacementCommand.py  # "Move / Rotate PCB" — reposition PCB + its pad CPs
├── leadframe/
│   ├── LeadframeCommand.py     # Leadframe Configurator + Center Leadframe commands
│   ├── LeadframeConfigurator.py# QFN / QFP / BGA configuration dialog
│   ├── LeadframeLibrary.py     # Online library browser (MirrorSemi STEP import)
│   └── LayeronLeadframe.py     # Layer-on-Leadframe command & dialog
├── housing/
│   ├── HousingCommand.py       # Housing Configurator command
│   └── HousingConfigurator.py  # Housing configuration dialog
├── wirebond/
│   ├── WirebondCommand.py      # Wire bonding commands (manual, cancel, browser, bumps)
│   ├── WirebondConfigurator.py # Wire bonding session configuration dialog
│   ├── ManualWireBonding.py    # Interactive bonding session (solid swept-pipe geometry)
│   ├── WireBumpConfigurator.py # Bump shape configurator with netlist browser
│   ├── ContactPointTool.py     # "Define Contact Points" command
│   ├── ContactPointPanel.py    # Contact Point Browser dock panel (die / housing / PCB)
│   ├── SetContactPointsOnFaceCommand.py  # Grid-based face contact point placement
│   └── Wirebon_Confi_Support.py# Prerequisite checks for wire bond activation
├── session/
│   ├── SessionManager.py       # Session record / persist / restore logic (.dipas)
│   ├── SaveSessionCommand.py   # Save action
│   ├── LoadSessionCommand.py   # Load & replay action
│   └── SessionMenuCommand.py   # Combined Save/Load dropdown toolbar button
├── ui/
│   ├── LayerSelector.py        # Layer selection dialog (used during GDS import)
│   ├── LODManager.py           # LOD state machine + background load workers (QThread)
│   ├── DetailLayerPanel.py     # Detail Layer Control dock panel (LOD panel)
│   ├── ExtendedPropertyPanel.py
│   └── LayeronLeadframeConfigurator.py
├── help/
│   ├── HelpGuideCommand.py     # Modern in-app help (sidebar navigation + QTextBrowser)
│   └── AboutCommand.py         # About dialog
└── resources/
    ├── gds/ALL_LNA.gds         # Sample GDS file for testing
    ├── icons/                  # SVG/PNG toolbar icons (see table below)
    ├── html/                   # HTML content for the in-app help guide
    └── workflow.svg            # Workflow overview diagram
```

### Icon Reference

| File | Used by |
|---|---|
| `PCB_Import.svg` | PCB Import |
| `PCB_Move.svg` | Move / Rotate PCB |
| `Load GDS.png` | Load GDSII |
| `Performance_Mode.svg` | Toggle Performance Mode / Detail Layer Control |
| `Layer_Slider.svg` | Layer Slider |
| `Leadframe_Library.svg` | Leadframe Library |
| `Chip_Transform.svg` | Move / Rotate Chip |
| `Set_Contact_Points.svg` | Set Contact Points on Face |
| `ContactPoint_Browser.svg` | Contact Point Browser |
| `Wire_bonding.png` | Wire Bond |
| `Wire_Bump.svg` | Wire Bump Configurator |
| `Cancel_Wirebonding.svg` | Cancel Wire Bonding |
| `Session.svg` | Session ▾ dropdown |
| `Toggle_Advanced.svg` | Advanced Tools ▾ dropdown |
| `Help_Guide.svg` | Help Guide |
| `Leadframe_Configurator.png` | Leadframe Configurator |
| `Center_Leadframe.svg` | Center Leadframe |
| `Housing_Configurator.png` | Housing Configurator |
| `Layer on Leadframe.png` | Layer on Leadframe |
| `Define_Contact_Points.svg` | Define Contact Points |

---

## Quick Setup

See **[INSTALL.md](INSTALL.md)** for the full step-by-step guide.

**Short version:**

```bash
# Clone into FreeCAD's user Mod folder (Windows)
git clone <repository-url> "%APPDATA%/FreeCAD/Mod/DI-PASSIONATE-FreeCAD"

# Install the gdstk dependency into FreeCAD's Python
"C:/Program Files/FreeCAD 1.1/bin/python.exe" -m pip install gdstk
```

Restart FreeCAD — the **Chip-Packaging Workbench** will appear in the workbench selector.

---

## Design Notes / Mindmap

<https://lucid.app/lucidspark/ebb96ac9-c6d3-408a-9ead-51c1aa83efa1/edit?invitationId=inv_3ef9b6cf-fcc6-4717-8b34-9a1598ceaaf7>

A possible target UI showing a configuration module for chip-packaging elements:

<img width="511" height="334" alt="target UI concept" src="https://github.com/user-attachments/assets/5ac820ee-de2e-4051-97c5-c6499160bba8" />

An example of a bonded chip within a package:

<img width="230" height="300" alt="bonded chip 1" src="https://github.com/user-attachments/assets/49d8373b-136f-4cf7-80d6-4976a90abba1" /> <img width="230" height="300" alt="bonded chip 2" src="https://github.com/user-attachments/assets/59d32864-7f0b-452e-8055-ff854130013b" />

---

## Contributing / Feedback

Issues and pull requests are welcome. Please open an issue describing any bug or feature request before submitting a large PR.
