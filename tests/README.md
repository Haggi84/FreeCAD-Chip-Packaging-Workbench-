# Headless smoke tests

Fast, no-GUI regression checks for the pieces of this plugin that broke
repeatedly during development — run these before/after touching wire bond
geometry, the leadframe builder, GDS import, mesh baking, or pin numbering.

## Running

```
"C:\Program Files\FreeCAD 1.1\bin\freecadcmd.exe" tests\run_all.py
```

Exit code is `0` if every check passed, `1` otherwise (suitable for CI).
A full report is also written to `tests/results.log` — read that file
directly if console output looks garbled or cut off (see note below).

## What's covered

| File | Covers |
|---|---|
| `test_wirebond_geometry.py` | `create_bond_wire_3d()` — ball/wedge, cut/solid, spline/JEDEC profiles, degenerate stacked contacts |
| `test_leadframe_build.py` | `core.leadframe.build_leadframe()` — QFN lead count, tags, paddle, contact points |
| `test_gds_import.py` | `core.Core_Functionality.load_gds()` against a small sample file |
| `test_performance_mode.py` | Fast-mesh baking (`_bake_layer_mesh`) — valid mesh output, idempotent re-bake |
| `test_pin_numbering.py` | Perimeter-walk auto-numbering — full 1..N coverage, direction sensitivity |
| `test_via_clustering.py` | `core.via_clustering.cluster_boxes()` — tightly packed vias merge into one block, well-separated arrays stay separate, gap parameter actually controls the behaviour |
| `test_housing_build.py` | `core.housing.build_housing()` — outer shell, cavity cut, alignment posts, fused final housing, optional lid, with/without lid |
| `test_perf_mode_sync.py` | Integration between the 4 GDS performance mechanisms — `sync_new_layer_display()` respects current fast-mesh state, via layers dispatch correctly, `invalidate_layer_mesh()`/`invalidate_via_block()` actually clear stale caches |

## Bugs this suite has already caught

- **`core/housing.py`**: the alignment-post sketch and its extrusion were
  both named `"AlignmentPosts"`. FreeCAD silently renamed the extrusion to
  `"AlignmentPosts001"`, so any `doc.getObject("AlignmentPosts")` lookup
  actually got the flat sketch (zero volume) instead of the solid. Fixed by
  naming the sketch `"AlignmentPostsSketch"` (matching the existing
  `LidSketch`/`Lid` naming pattern already used elsewhere in the same file).
- **`tests/test_gds_import.py`** (a bug in the test itself, not the plugin):
  initially built `selected_layers` as raw `(layer_id, datatype)` tuples,
  but `load_gds()` expects a list of dicts with `layer_id`/`datatype` keys.
  The test caught its own mistake — exactly the point of having one.
- **`leadframe/LeadframeLibrary.py`**: `import ImportGui` at module level.
  Unlike `FreeCADGui.addCommand` (which merely doesn't exist headlessly),
  `ImportGui` cannot even be *imported* in console mode — the bare import
  statement raises `ImportError: Cannot load Gui module in console
  application.` unconditionally. Fixed by moving the import inside the one
  function that actually uses it (`_import_into_freecad`), so it's only
  evaluated when a real GUI session calls it.
- **`ui/LODManager.py`**: newly-loaded layers always forced full B-rep
  Detail mode (`set_layer_detail(existing, True)`), with no awareness of
  whether the document was currently in fast-mesh mode. A layer lazily
  loaded after the initial import would visually and performance-wise stick
  out from an already-meshed document. Fixed by routing through the new
  `sync_new_layer_display()` in `TogglePerformanceModeCommand.py`.
- **`gds/TogglePerformanceModeCommand.py` / `gds/ToggleViaDetailCommand.py`**:
  `_bake_layer_mesh()`/`_build_via_block()` permanently reuse whatever mesh
  or block already exists under a given name — neither ever checks whether
  the source Shape has changed since baking. `ui/DetailLayerPanel.py`'s
  bbox-simplify toggle mutates a layer's Shape in place, so without
  invalidation the cached mesh/block would silently keep showing geometry
  baked from the Shape's *previous* contents indefinitely. Fixed with new
  `invalidate_layer_mesh()`/`invalidate_via_block()` functions, called from
  `_simplify_layer()`/`_restore_layer()`.
- **`gds/ToggleViaDetailCommand.py`**: `sync_new_via_layer()` (written for
  the fix above) bailed out immediately if `obj.ViewObject` was `None`,
  skipping block creation entirely — inconsistent with the analogous
  `sync_new_layer_display()`, which has no such guard and lets
  `_bake_layer_mesh()`'s own internal try/except handle a missing
  ViewObject gracefully while still creating the underlying object. Found
  by `test_perf_mode_sync.py` itself, immediately after being written.

## Every command module is now importable headlessly

Every `FreeCADGui.addCommand(...)` call in the plugin (30 call sites across
21 files) is now guarded with `if FreeCAD.GuiUp:`, and the one hard blocker
above (`ImportGui`) is deferred to point of use. This means the entire
import chain `InitGui.py` walks at workbench load time now succeeds under
`freecadcmd` with zero errors — not just the handful of files these tests
happen to touch. That headroom is what let `_harness.load_module_from_file`
stop being strictly necessary for new command files (plain `import` now
works), though it's kept for tests that only want one file's code without
pulling in a whole package's transitive imports.

## Why this exists

These four areas each went through multiple rounds of "looked fine, broke
again" during development — most notably the wire bond cut/trim geometry,
which took roughly eight iterations to stop penetrating or under-cutting
pads. None of that was caught until a human looked at a screenshot. These
tests exist to catch the next regression in under a minute instead of a full
manual FreeCAD session.

## Known quirks of `freecadcmd`

- **No GUI at all.** `FreeCAD.GuiUp` is `False`, `obj.ViewObject` is always
  `None`, and `FreeCADGui.addCommand` doesn't exist. Production code that
  needs to stay headless-safe guards these with `if FreeCAD.GuiUp:` (see
  `core/leadframe.py`, `gds/TogglePerformanceModeCommand.py`,
  `leadframe/PinNumberingCommand.py`).
- **Command files can't be imported normally.** Every FreeCAD command file
  in this plugin calls `FreeCADGui.addCommand(...)` at import time. Even
  with the `GuiUp` guard, importing a package (`import gds.SomeCommand`)
  still walks that package's `__init__.py`, which often imports *other*
  sibling command files that would also need patching. `_harness.py`'s
  `load_module_from_file()` loads a single `.py` file directly, bypassing
  the package entirely, so tests only depend on the one file they're
  actually exercising.
- **`__name__` is not `"__main__"`.** freecadcmd imports the target script
  as a module named after its filename, so the usual
  `if __name__ == "__main__":` guard never fires — `run_all.py` calls
  `main()` unconditionally instead.
- **stdout ordering can look wrong.** FreeCAD's native console output
  (recompute progress bars, GDS/mesh diagnostics) flushes through a
  different buffer than Python's own `print()`, so piped/redirected output
  can appear jumbled or truncated even when every test actually passed.
  This is why results are also written straight to `tests/results.log`.
