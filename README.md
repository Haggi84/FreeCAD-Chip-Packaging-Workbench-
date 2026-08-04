# Chip-Packaging Workbench for FreeCAD

![Version](https://img.shields.io/badge/version-0.9.0-green?style=flat-square)
![FreeCAD](https://img.shields.io/badge/FreeCAD-1.1-blue?style=flat-square)
![Python](https://img.shields.io/badge/Python-3.11-yellow?style=flat-square)
![License](https://img.shields.io/badge/license-GPL--3.0--or--later-lightgrey?style=flat-square)
![Semantic Versioning](https://img.shields.io/badge/semver-2.0.0-informational?style=flat-square)
![Tests](https://img.shields.io/badge/tests-451%20checks-brightgreen?style=flat-square)

**An open-source FreeCAD workbench for chip-packaging design, developed as part of the BMBF research project DI-PASSIONATE.**

It covers the full chip-assembly flow in one environment: importing GDSII layouts and PCBs,
placing dice, generating leadframes and housings, routing conductor traces, planning wire
bonds and bumps, and saving the result as a native FreeCAD document.

> **Project status:** actively developed and not yet API-stable. Interfaces and file
> layouts may change between minor versions.

<p align="center">
  <img width="230" alt="Bonded die inside a package" src="https://github.com/user-attachments/assets/49d8373b-136f-4cf7-80d6-4976a90abba1" />
  <img width="230" alt="Bonded die, alternate view" src="https://github.com/user-attachments/assets/59d32864-7f0b-452e-8055-ff854130013b" />
</p>
<p align="center"><em>A die bonded into a leadframe package, assembled entirely in the workbench.</em></p>

---

## Contents

- [Requirements](#requirements)
- [Installation](#installation)
- [Feature Reference](#feature-reference)
- [Typical Workflow](#typical-workflow)
- [GDSII Import](#gdsii-import)
- [Trace Routing](#trace-routing)
- [PCB Integration](#pcb-integration)
- [Wire Bonding](#wire-bonding)
- [Contact Point System](#contact-point-system)
- [Supported File Formats](#supported-file-formats)
- [Session Save and Load](#session-save-and-load)
- [Development](#development)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [License](#license)

---

## Requirements

| Component | Version | Notes |
|---|---|---|
| FreeCAD | 1.1 or newer | Ships its own Python 3.11 — no separate install required |
| `gdstk` | any recent | Required for GDSII parsing; install into FreeCAD's Python |
| Draft workbench | bundled | Required by the Interactive Router (ships with FreeCAD) |
| Internet access | — | Only for the online leadframe library |

## Installation

Full instructions, including developer tooling and remote debugging, are in
**[INSTALL.md](INSTALL.md)**. Short version:

```powershell
# 1. Clone into FreeCAD's user Mod folder
git clone <repository-url> "$env:APPDATA\FreeCAD\Mod\DI-PASSIONATE-FreeCAD"

# 2. Install the GDSII dependency into FreeCAD's own Python
& "C:\Program Files\FreeCAD 1.1\bin\python.exe" -m pip install gdstk
```

Restart FreeCAD and choose **Chip-Packaging Workbench** in the workbench selector.

---

## Feature Reference

Tools are grouped into toolbars matching the design flow. Each toolbar carries a short
caption (Tech, Import, Render, Package, Bonding, Routing, Workbench).

### Technology

| Tool | Description |
|---|---|
| **Technology Configuration** | Select the active PDK profile (`.lyp` / `.map` / stackup `.xml`) once and reuse it across all import dialogs. The status bar shows which files resolved. |

### Import

| Tool | Description |
|---|---|
| **PCB Import** | Load a PCB from STEP; copper pad faces are auto-detected as ContactPoints. |
| **Move / Rotate PCB** | Reposition a loaded PCB together with all of its pad ContactPoints. |
| **Load GDSII** | Import `.gds` with KLayout colours, optional technology map and stackup for true Z-heights. Supports level-of-detail import. |
| **Import Chip Proxy** | Create a lightweight stand-in for a die — footprint, real stack thickness and bond-pad positions only. Loads in well under a second on full-chip layouts that take minutes to tessellate in full. |

### Rendering

| Tool | Description |
|---|---|
| **Toggle Performance Mode** | Swap GDS layers between full B-rep and a fast mesh representation. |
| **Toggle VIA Detail** | Replace dense via arrays with clustered blocks to keep the viewport responsive. |
| **Detail Layer Control** | Dock panel showing every layer's load state; promote routing layers to full geometry on demand. |
| **Layer Slider** | Step through the layer stack from bottom to top for a quick review. |

### Package Assembly

| Tool | Description |
|---|---|
| **Move / Rotate Chip** | Translate and rotate chip geometry as a group. Scopes: GDS objects, a single chip proxy (optionally with package and PCB), the current selection, or the whole document. |
| **Leadframe Library** | Browse and import STEP package models from the MirrorSemi online catalogue. |
| **Set Contact Points on Face** | Grid-based placement: pick faces, generate a UV grid, select points, confirm. |
| **Interactive Contact Point** | Place individual contact points by clicking directly in the 3-D view. |
| **Contact Point Browser** | Dock panel listing all contact points by group, with hover highlighting. |

### Trace Routing

| Tool | Description |
|---|---|
| **Interactive Route** | KiCad-style live router — see [Trace Routing](#trace-routing). |
| **Trace Routing** | Grid and search based point-to-point router with angle presets. |
| **Confirm / Abort / End** | Contextual toolbar shown only while a routing session is active. |

### Wire Bonding

| Tool | Description |
|---|---|
| **Wire Bond** | Interactive bonding session: click a die pad, then a package or PCB pad; a solid bond wire is created. |
| **Wire Bump Configurator** | Place parametric bumps (ball, wedge, stitch, nail head) at wire endpoints via a netlist browser. |
| **Confirm / Abort** | Contextual toolbar shown only while a bonding session is active. |

### Advanced Tools (dropdown)

| Tool | Description |
|---|---|
| **Leadframe Configurator** | Generate parametric QFN, QFP or BGA leadframes. |
| **Center Leadframe** | Align the leadframe to the imported die geometry. |
| **Housing Configurator** | Generate a mould-compound body around the leadframe. |
| **Add Lid** | Add or resize a lid on an existing housing. |
| **Layer on Leadframe** | Scale, rotate and place GDS layers onto a leadframe. |
| **Define Contact Points** | Batch-place markers at the top-face centre of selected layer objects. |
| **Pin Numbering** | Generate pin-number labels around a leadframe. |

### Workbench

**Session ▾** (save / load a design as a native `.FCStd`), **Advanced Tools ▾**,
**Help Guide** (in-app documentation), and **About**.

---

## Typical Workflow

![Chip-packaging workflow diagram](resources/workflow.svg)

| Step | Tool | Purpose |
|---|---|---|
| 1 | Technology Configuration | Select the PDK profile once |
| 2 | Load GDSII *or* Import Chip Proxy | Bring in the die — full geometry, or a fast stand-in for layout work |
| 3 | Leadframe Library *or* Leadframe Configurator | Import or generate the package |
| 4 | Center Leadframe | Align package to die |
| 5 | Move / Rotate Chip | Fine-tune die placement |
| 6 | Housing Configurator *(optional)* | Add mould compound and lid |
| 7 | Set Contact Points on Face | Define bonding locations |
| 8 | Wire Bond → Wire Bump Configurator | Create bond wires and end bumps |
| 9 | Interactive Route *(optional)* | Route conductor traces on the board or package |
| 10 | Session ▾ → Save | Store the assembly as `.FCStd` |

---

## GDSII Import

The layer selector exposes the import options that matter for large layouts:

| Option | Effect |
|---|---|
| Match KLayout colours | Apply exact fill and frame colours from the `.lyp` |
| Highlight bondable pads | Render top-metal and PIN layers in gold |
| 3-D extrusion | Extrude each layer to its real Z-height from the stackup |
| Contacts-only 3-D | Full geometry for bond-pad layers only; everything else collapses to a bounding solid |
| Auto PIN contact detection | Create ContactPoint markers on top PIN layers automatically |

### Level of Detail

With LOD active, layers are categorised before anything is loaded:

| Category | Import behaviour |
|---|---|
| Contact layers (PIN, COMP, top/bottom metal) | Full 3-D geometry, loaded immediately |
| Pure PIN-marker layers | Flat 2-D polygons, loaded immediately |
| Fill / dummy metal | Bounding solid only, never tessellated |
| Routing layers | Transparent placeholder at the correct Z — loaded on demand |

Routing layers are promoted to full geometry from the **Detail Layer Control** panel,
either individually, by dragging a Z-cursor through the stack, or all at once in
background threads. Initial import stays in the seconds range for chips that would
otherwise take minutes.

---

## Trace Routing

Two routers are provided; both produce identical `Trace_NNN` objects, so they can be
mixed freely in one design.

### Interactive Route (recommended)

A live router modelled on KiCad's. Select the face to route on, start the tool, and click
a start point — the trace then **follows the cursor** as a two-segment 45° head, walking
around existing copper in its way. It never pathfinds blindly: what you see is what gets
built.

| Input | Action |
|---|---|
| Left click | Fix the current head |
| `/` | Flip the corner posture |
| `Backspace` | Undo the last segment |
| `Esc` | Finish the trace |

Key properties:

- **Works on any face of a 3-D body** — horizontal, vertical, slanted or curved. Routing
  is done in the face's own parameter space scaled to millimetres, which is an exact
  isometry for planes and cylinders, so widths, clearances and 45° angles stay physically
  correct. On a double-curved face the tool reports its distortion and warns that those
  quantities only hold approximately.
- **Exact copper outlines** as obstacles, not bounding boxes — essential on real boards,
  where a diagonal trace's bounding box can span an area many times its actual footprint.
- **Same-net awareness**: the pad a trace starts or ends on does not block it.
- **Stays on the face**: routes cannot leave the trimmed boundary of the routing surface.

### Trace Routing (grid based)

The original point-to-point router: samples a grid on the selected face, builds a
visibility graph and searches it with an angle-constrained A*. Offers preferred heading
grids (45°, 90° Manhattan, 30°), corner rounding, and multi-leg traces with confirm/abort.
Useful when you want a computed route between two fixed points rather than a hand-steered
one.

---

## PCB Integration

1. **PCB Import** — select a `.step` / `.stp` file. The board is placed as `PCB_Board` and
   copper pads are detected as ContactPoints (`PCB_Pad_NNN`).
2. **Move / Rotate PCB** — adjust position and rotation; the board and its pads move together.
3. Bond or route from die pads to PCB pads exactly as to leadframe pads.

Pad detection uses near-horizontal faces in the top 4 % of the board's Z extent, with an
area between 0.01 mm² and 150 mm².

---

## Wire Bonding

Start a bonding session, then click a die-side contact point followed by a package or PCB
contact point. A swept solid bond wire is created per pair.

| Profile | Description |
|---|---|
| Realistic spline loop | Smooth multi-point BSpline loop |
| JEDEC trapezoid | Straight rise, flat kink, vertical drop |

Bond ends can be finished with parametric bumps — ball, wedge, stitch or nail head — via
the **Wire Bump Configurator**, which includes a live cross-section preview and a netlist
browser of all bond wires.

---

## Contact Point System

Contact points are marker objects carrying a `ContactPoint` position that bonding and
routing tools snap to.

| Type | Name pattern | Colour | Created by |
|---|---|---|---|
| Die side | `ContactPoint_NNN` | Orange | Auto PIN detection, Define Contact Points, Chip Proxy import |
| Package / housing | `contact_point_housing_NNN` | Yellow | Set Contact Points on Face, Interactive Contact Point |
| PCB | `PCB_Pad_NNN` | Yellow | PCB Import (auto-detected) |

---

## Supported File Formats

| Format | Purpose |
|---|---|
| `.gds` | GDSII layout from KLayout, Cadence and similar tools |
| `.lyp` | KLayout layer properties — colours and visibility |
| `.map` | Technology map — layer names and EDI types (PIN, NET, VIA, FILL) |
| `.xml` | KLayout stackup — per-layer Zmin/Zmax from the PDK |
| `.step` / `.stp` | Package and PCB models |
| `.FCStd` | Native FreeCAD document — the complete design |

The IHP Open PDK, including sample technology files, is available at
<https://github.com/IHP-GmbH/IHP-Open-PDK>. Sample layouts for testing ship in
`samples/` and `resources/gds/`.

---

## Session Save and Load

**Save Design Session** writes the active document as a native `.FCStd`, preserving the
exact current state including manual edits — not a replay of recognised actions.

Workbench-only display state that does not live on a document object (fast-mesh mode, VIA
detail mode, lazy layer-loading state) is captured alongside the document and restored on
open, whether reopened via the Session menu or FreeCAD's own File → Open. The mechanism is
an extensible provider registry in `session/WorkbenchState.py`.

---

## Development

### Project Structure

```
DI-PASSIONATE-FreeCAD/
├── InitGui.py              Workbench registration, toolbars, dropdown menus
├── version.py              Single source of truth for the version number
├── Get_Path.py             Icon and resource path helpers
├── compat.py               Qt compatibility shim
├── core/                   Algorithms and geometry — no Qt, no GUI
│   ├── Core_Functionality.py   GDS parsing, shape building, PIN detection
│   ├── chip_proxy.py           Fast tessellation-free chip stand-ins
│   ├── lod_import.py           Level-of-detail categorisation
│   ├── leadframe.py            Leadframe geometry
│   ├── housing.py              Housing geometry
│   ├── via_clustering.py       Via-array simplification
│   ├── routing_frame.py        2-D routing frame for flat and curved faces
│   ├── trace_obstacles.py      Exact copper outlines as keep-outs
│   ├── trace_walkaround.py     KiCad-style posture head and walk-around
│   ├── trace_routing.py        Grid, visibility graph and A* router
│   ├── TechConfig.py           Active PDK profile
│   ├── gds_io/                 GDS caching, inspection, extraction
│   ├── tech/                   Technology file parsers and stackup handling
│   └── geometry/               Polygon, transform and mesh utilities
├── gds/                    GDS import, chip transform, rendering commands
├── pcb/                    PCB import and placement
├── leadframe/              Leadframe configurator, library, pin numbering
├── housing/                Housing configurator and lid
├── wirebond/               Bonding session, bumps, contact point tools
├── routing/                Interactive and grid routers
├── session/                Document state save and restore
├── ui/                     Dialogs and dock panels
├── help/                   In-app help and about
├── tests/                  Headless test suite
└── resources/              Icons, sample layouts, help content, PDK files
```

The codebase separates **algorithms** (`core/`, Qt-free and headlessly testable) from
**commands** (everything else, which owns dialogs, selection and FreeCAD command
registration). New functionality should follow the same split.

### Running the Tests

The suite runs headlessly in FreeCAD's own interpreter — no pytest required, since the
tests need a live FreeCAD and OCCT:

```powershell
& "C:\Program Files\FreeCAD 1.1\bin\freecadcmd.exe" tests\run_all.py
```

451 checks across 18 modules covering geometry construction, GDS import, level-of-detail
state, routing, obstacle handling and session state. Results are also written to
`tests/results.log`. The runner exits non-zero on failure, so it is suitable for CI.

---

### Design Notes

Concept sketches and the design mindmap for the workbench are kept here:
<https://lucid.app/lucidspark/ebb96ac9-c6d3-408a-9ead-51c1aa83efa1/edit?invitationId=inv_3ef9b6cf-fcc6-4717-8b34-9a1598ceaaf7>

<img width="420" alt="Target UI concept for the packaging configuration module" src="https://github.com/user-attachments/assets/5ac820ee-de2e-4051-97c5-c6499160bba8" />

---

## Roadmap

- Traces that wrap across multiple faces of a body (3-D MID / laser direct structuring)
- Export of assemblies for thermal simulation
- Expanded material assignment
- Additional online component libraries

---

## Contributing

Issues and pull requests are welcome. For anything substantial, please open an issue
describing the problem or proposal first. Contributions should keep the `core/` versus
command-layer split described above and add headless tests for new algorithmic code.

---

## License

This project is licensed under the **GNU General Public License v3.0 or later**.
See [LICENSE](LICENSE) for the full text.
