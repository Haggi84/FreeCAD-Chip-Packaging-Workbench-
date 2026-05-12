"""Online leadframe library browser for MirrorSemi CAD resources."""

import os
import re
import tempfile
import urllib.request
from typing import List, Optional
from urllib.parse import urljoin

import FreeCAD
import FreeCADGui
import ImportGui
from compat import QtCore, QtGui, QtWidgets, qenum_int

from Get_Path import get_icon

DEFAULT_LIBRARY_URL = "https://www.mirrorsemi.com/CAD.html"

ACCEPTED_DOWNLOAD_EXTS = (
    ".stp",
    ".step",
    ".igs",
    ".iges",
    ".dxf",
    ".dwg",
    ".fcstd",
    ".zip",
    ".rar",
    ".7z",
)

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")

FORMAT_GROUPS = {
    "All formats": None,
    "3D models (STEP / IGES)": (".stp", ".step", ".igs", ".iges"),
    "2D drawings (DXF / DWG)": (".dxf", ".dwg"),
    "FreeCAD files": (".fcstd",),
    "Archives (ZIP / RAR / 7Z)": (".zip", ".rar", ".7z"),
}


class LeadframeEntry:
    """Simple data container for a leadframe library entry."""

    def __init__(self, name: str, url: str, package_page_url: Optional[str] = None):
        self.name = name
        self.url = url
        # URL of the per-package HTML detail page (e.g. https://…/M-QFN8W.65.html)
        self.package_page_url = package_page_url

    def __repr__(self) -> str:  # pragma: no cover
        return f"LeadframeEntry(name={self.name}, url={self.url})"


def fetch_leadframe_entries(library_url: str = DEFAULT_LIBRARY_URL) -> List[LeadframeEntry]:
    """
    Fetch the MirrorSemi CAD page and collect downloadable entries.

    The page is a table where each row contains one package's HTML detail page
    link plus all of its CAD file download links.  We parse row-by-row so that
    every CAD entry keeps a reference to its package detail page (used later for
    photo previews).
    """
    with urllib.request.urlopen(library_url, timeout=15) as response:
        html_bytes = response.read()
    html = html_bytes.decode("utf-8", errors="ignore")

    entries: List[LeadframeEntry] = []

    for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", html, re.IGNORECASE | re.DOTALL):
        all_hrefs = re.findall(r'href="([^"]+)"', row, re.IGNORECASE)

        # CAD file links in this row
        cad_hrefs = [
            h for h in all_hrefs
            if h.lower().split("?")[0].endswith(ACCEPTED_DOWNLOAD_EXTS)
        ]
        if not cad_hrefs:
            continue

        # Package detail page link in the same row (local *.html, not external)
        html_hrefs = [
            h for h in all_hrefs
            if h.lower().endswith(".html") and not h.startswith("http")
        ]
        package_page_url = urljoin(library_url, html_hrefs[0]) if html_hrefs else None

        for href in cad_hrefs:
            full_url = urljoin(library_url, href)
            file_name = os.path.basename(href.split("?")[0]) or "leadframe"
            entries.append(LeadframeEntry(
                name=file_name,
                url=full_url,
                package_page_url=package_page_url,
            ))

    return entries


def _fetch_package_detail(package_page_url: str):
    """
    Fetch a per-package detail page and return ``(photos, info_text)``.

    *photos* is a list of absolute image URLs (product photos only).
    *info_text* is a plain-text summary of any spec table rows, headings, and
    description paragraphs found on the page — ready to display in the preview
    panel.

    Both parts are derived from a single HTTP request so the page is only
    downloaded once per selection.
    """
    import html as _html_mod

    pkg_stem = os.path.splitext(os.path.basename(package_page_url.split("?")[0]))[0]

    with urllib.request.urlopen(package_page_url, timeout=15) as resp:
        raw_html = resp.read().decode("utf-8", errors="ignore")

    # ── photos ───────────────────────────────────────────────────────────────
    srcs = re.findall(r'<img[^>]+src="([^"]+)"', raw_html, re.IGNORECASE)
    photos: List[str] = []
    for src in srcs:
        if pkg_stem.lower() in src.lower() and any(src.lower().endswith(e) for e in IMAGE_EXTS):
            photos.append(urljoin(package_page_url, src))

    # ── text info ─────────────────────────────────────────────────────────────
    def _strip(fragment: str) -> str:
        text = re.sub(r"<[^>]+>", "", fragment)
        text = _html_mod.unescape(text)
        return re.sub(r"[ \t]+", " ", text).strip()

    lines: List[str] = []

    # Headings (h1–h3)
    for m in re.finditer(r"<h[1-3][^>]*>(.*?)</h[1-3]>", raw_html, re.IGNORECASE | re.DOTALL):
        t = _strip(m.group(1))
        if t:
            lines.append(t)

    # Description paragraphs
    for m in re.finditer(r"<p[^>]*>(.*?)</p>", raw_html, re.IGNORECASE | re.DOTALL):
        t = _strip(m.group(1))
        if len(t) > 15:          # skip tiny/empty paragraphs
            lines.append(t)

    # Spec table rows that look like key–value pairs (exactly 2 cells)
    for row_m in re.finditer(r"<tr\b[^>]*>(.*?)</tr>", raw_html, re.IGNORECASE | re.DOTALL):
        cells = re.findall(
            r"<t[dh][^>]*>(.*?)</t[dh]>", row_m.group(1), re.IGNORECASE | re.DOTALL
        )
        if len(cells) == 2:
            key = _strip(cells[0])
            val = _strip(cells[1])
            if key and val:
                lines.append(f"{key}: {val}")

    info_text = "\n".join(lines)
    return photos, info_text


_DOWNLOAD_TIMEOUT_S = 30
_DOWNLOAD_MAX_BYTES = 50 * 1024 * 1024  # 50 MB


def _download_to_temp(entry: LeadframeEntry) -> str:
    target_dir = tempfile.mkdtemp(prefix="leadframe_download_")
    target_name = os.path.basename(entry.url.split("?")[0]) or entry.name
    target_path = os.path.join(target_dir, target_name)
    with urllib.request.urlopen(entry.url, timeout=_DOWNLOAD_TIMEOUT_S) as response:
        data = response.read(_DOWNLOAD_MAX_BYTES + 1)
    if len(data) > _DOWNLOAD_MAX_BYTES:
        raise ValueError(f"Download exceeded the {_DOWNLOAD_MAX_BYTES // (1024 * 1024)} MB size limit.")
    with open(target_path, "wb") as f:
        f.write(data)
    return target_path


def _bbox_of(objects):
    """
    Return (xmin, ymin, zmin, xmax, ymax, zmax) for a collection of FreeCAD
    objects, or None if no valid bounding box could be found.
    """
    xmin = ymin = zmin = float("inf")
    xmax = ymax = zmax = float("-inf")
    found = False
    for obj in objects:
        try:
            bb = obj.Shape.BoundBox
            if not bb.isValid():
                continue
            xmin = min(xmin, bb.XMin); xmax = max(xmax, bb.XMax)
            ymin = min(ymin, bb.YMin); ymax = max(ymax, bb.YMax)
            zmin = min(zmin, bb.ZMin); zmax = max(zmax, bb.ZMax)
            found = True
        except Exception:
            continue
    return (xmin, ymin, zmin, xmax, ymax, zmax) if found else None


def _place_imported_package(doc, objects_before):
    """
    Translate newly imported package objects so that:
    - Their XY centre aligns with the XY centre of any existing scene geometry
      (GDS layers, leadframe, etc.).  Falls back to (0, 0) when the scene is
      empty.
    - Their bottom face (ZMin) rests at Z = 0, so the package sits on the same
      plane as the die and leadframe rather than being centred through Z = 0.
    """
    new_objects = [o for o in doc.Objects if o not in objects_before]
    if not new_objects:
        return

    pkg_bb = _bbox_of(new_objects)
    if pkg_bb is None:
        return
    xmin, ymin, zmin, xmax, ymax, zmax = pkg_bb
    pkg_cx = (xmin + xmax) / 2
    pkg_cy = (ymin + ymax) / 2

    # XY target: centre of existing scene objects (ignore contact-point markers)
    existing = [
        o for o in objects_before
        if not getattr(o, "IsContactPoint", False)
    ]
    scene_bb = _bbox_of(existing)
    if scene_bb is not None:
        sx1, sy1, _, sx2, sy2, _ = scene_bb
        target_cx = (sx1 + sx2) / 2
        target_cy = (sy1 + sy2) / 2
    else:
        target_cx = target_cy = 0.0

    dx = target_cx - pkg_cx
    dy = target_cy - pkg_cy
    dz = -zmin           # lift bottom to Z = 0

    if abs(dx) < 1e-6 and abs(dy) < 1e-6 and abs(dz) < 1e-6:
        return

    offset = FreeCAD.Vector(dx, dy, dz)
    for obj in new_objects:
        try:
            pl = obj.Placement
            pl.Base = pl.Base.add(offset)
            obj.Placement = pl
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Package-orientation helpers & interactive panel
# ---------------------------------------------------------------------------

def _find_gds_document() -> Optional[str]:
    """
    Return the name of the open GDS document, or None if none is found.
    Prefers 'GDSII_Document'; falls back to any document whose objects
    start with 'Layer_' (the GDS import naming convention).
    """
    docs = FreeCAD.listDocuments()
    if "GDSII_Document" in docs:
        return "GDSII_Document"
    for name, doc in docs.items():
        if any(o.Name.startswith("Layer_") for o in doc.Objects):
            return name
    return None


def _doc_combined_bbox(doc) -> Optional[object]:
    """
    Return a FreeCAD BoundBox spanning all valid shape objects in *doc*,
    or None if the document has no renderable geometry.
    """
    bb = None
    for obj in doc.Objects:
        try:
            if hasattr(obj, "Shape") and obj.Shape and not obj.Shape.isNull():
                candidate = obj.Shape.BoundBox
                if candidate.isValid():
                    bb = candidate if bb is None else bb.united(candidate)
        except Exception:
            continue
    return bb


def _rotate_pkg_doc(doc, rotation: "FreeCAD.Rotation"):
    """
    Apply *rotation* to every shape object in *doc*, pivoting around the
    combined bounding-box centre.  Afterwards, translate the model so its
    new ZMin sits exactly at Z = 0 (bottom face on the ground plane).
    """
    bb = _doc_combined_bbox(doc)
    if bb is None:
        return

    center = bb.Center
    shape_objs = [
        o for o in doc.Objects
        if hasattr(o, "Shape") and o.Shape and not o.Shape.isNull()
    ]
    for obj in shape_objs:
        pl = obj.Placement
        new_base = rotation.multVec(pl.Base - center) + center
        new_rot  = rotation.multiply(pl.Rotation)
        obj.Placement = FreeCAD.Placement(new_base, new_rot)

    doc.recompute()

    # Lift so bottom rests at Z = 0
    bb2 = _doc_combined_bbox(doc)
    if bb2 and abs(bb2.ZMin) > 1e-6:
        dz = -bb2.ZMin
        for obj in shape_objs:
            pl = obj.Placement
            pl.Base = pl.Base + FreeCAD.Vector(0.0, 0.0, dz)
            obj.Placement = pl
        doc.recompute()


def _compute_rotation_to_z(normal: "FreeCAD.Vector") -> "FreeCAD.Rotation":
    """
    Return the FreeCAD Rotation that maps *normal* onto the Z+ axis (0,0,1).
    Handles the degenerate anti-parallel case with a 180° flip around X.
    """
    import math
    n = FreeCAD.Vector(normal).normalize()
    z = FreeCAD.Vector(0.0, 0.0, 1.0)
    dot = max(-1.0, min(1.0, n.dot(z)))

    if abs(dot - 1.0) < 1e-6:          # already Z+
        return FreeCAD.Rotation()
    if abs(dot + 1.0) < 1e-6:          # exactly Z- → flip 180° around X
        return FreeCAD.Rotation(FreeCAD.Vector(1.0, 0.0, 0.0), 180.0)

    axis  = n.cross(z).normalize()
    angle = math.degrees(math.acos(dot))
    return FreeCAD.Rotation(axis, angle)


_COLOR_FACE_PICK = (1.0, 0.55, 0.0, 1.0)   # orange — picked-face highlight


class _FaceSelectionObserver:
    """
    Thin FreeCAD Selection observer that fires *callback(obj, sub_name, face)*
    whenever the user selects a face sub-element in the 3D view.
    """

    def __init__(self, callback):
        self._cb = callback

    def addSelection(self, doc_name, obj_name, sub_name, _pnt):
        if not sub_name.startswith("Face"):
            return
        try:
            doc = FreeCAD.getDocument(doc_name)
            obj = doc.getObject(obj_name) if doc else None
            if obj and hasattr(obj, "Shape"):
                idx = int(sub_name[4:]) - 1     # "Face3" → index 2
                if 0 <= idx < len(obj.Shape.Faces):
                    self._cb(obj, sub_name, obj.Shape.Faces[idx])
        except Exception as exc:
            FreeCAD.Console.PrintWarning(f"[FaceObserver] {exc}\n")

    def removeSelection(self, *_): pass
    def setSelection(self, *_):    pass
    def clearSelection(self, *_):  pass


_COLOR_CONTACT_FACE = (0.0, 0.55, 1.0, 1.0)   # blue — die-attach face highlight


class _PackageOrientationPanel:
    """
    FreeCAD Task-Panel — three-step package setup:

    Step 1  Pick the top face → defines Z+ orientation.
            Press "Apply Rotation" to align the model.
    Step 2  (Optional) Pick the die-attach face → the face of the housing
            that will physically touch the bottom of the GDS chip layout.
            Only available after rotation is applied.
    Step 3  Choose destination: merge into the GDS document (aligned so
            the die-attach face meets the GDS bottom layer) or keep here.
            Cancel discards the package document.
    """

    # ── picking modes ─────────────────────────────────────────────────────────
    _MODE_TOP     = "top"
    _MODE_CONTACT = "contact"

    def __init__(self, pkg_doc_name: str, gds_doc_name: Optional[str]):
        self._pkg_doc_name  = pkg_doc_name
        self._gds_doc_name  = gds_doc_name

        # top-face state
        self._sel_obj       = None
        self._sel_sub       = None
        self._sel_face      = None
        self._sel_normal    = None
        self._orig_diffuse  = None          # saved colours for top-face highlight
        self._rotation_done = False

        # die-attach (contact) face state
        self._contact_obj_name  = None      # name of the FreeCAD object
        self._contact_face_idx  = None      # 0-based face index
        self._contact_face_z    = None      # world-space Z of face centre (pkg_doc)
        self._contact_diffuse   = None      # saved colours for contact-face highlight

        # current picking mode
        self._pick_mode = self._MODE_TOP

        self._observer = _FaceSelectionObserver(self._on_face_selected)
        FreeCADGui.Selection.addObserver(self._observer)
        self._build_form()

    # ── form ─────────────────────────────────────────────────────────────────

    def _build_form(self):
        self.form = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(self.form)
        root.setSpacing(6)

        # ── Step 1: top face ──────────────────────────────────────────────────
        root.addWidget(self._section("Step 1 — Pick the top face"))
        h1 = QtWidgets.QLabel(
            "Click any face on the package model.\n"
            "Its outward normal will become the new Z+ direction.\n"
            "The selected face is highlighted in orange."
        )
        h1.setWordWrap(True)
        root.addWidget(h1)

        self._face_lbl = QtWidgets.QLabel("<i>No face selected yet.</i>")
        self._face_lbl.setWordWrap(True)
        root.addWidget(self._face_lbl)

        self._apply_btn = QtWidgets.QPushButton("Apply Rotation")
        self._apply_btn.setEnabled(False)
        self._apply_btn.clicked.connect(self._apply_rotation)
        root.addWidget(self._apply_btn)

        self._rot_lbl = QtWidgets.QLabel("")
        self._rot_lbl.setWordWrap(True)
        root.addWidget(self._rot_lbl)

        # ── Step 2: die-attach face (optional) ───────────────────────────────
        sep1 = QtWidgets.QFrame(); sep1.setFrameShape(QtWidgets.QFrame.HLine)
        root.addWidget(sep1)
        root.addWidget(self._section("Step 2 — Pick die-attach face  (optional)"))

        h2 = QtWidgets.QLabel(
            "Click the face of the housing that should physically touch\n"
            "the bottom layer of the GDS chip layout when merged.\n"
            "The selected face is highlighted in blue.\n"
            "Only available after rotation is applied."
        )
        h2.setWordWrap(True)
        root.addWidget(h2)

        self._contact_lbl = QtWidgets.QLabel("<i>No die-attach face selected.</i>")
        self._contact_lbl.setWordWrap(True)
        root.addWidget(self._contact_lbl)

        # Toggle button to enter contact-face picking mode
        self._pick_contact_btn = QtWidgets.QPushButton("Pick Die-Attach Face")
        self._pick_contact_btn.setCheckable(True)
        self._pick_contact_btn.setEnabled(False)
        self._pick_contact_btn.toggled.connect(self._on_contact_btn_toggled)
        root.addWidget(self._pick_contact_btn)

        self._clear_contact_btn = QtWidgets.QPushButton("Clear Die-Attach Face")
        self._clear_contact_btn.setEnabled(False)
        self._clear_contact_btn.clicked.connect(self._clear_contact_face)
        root.addWidget(self._clear_contact_btn)

        # ── Step 3: destination ───────────────────────────────────────────────
        sep2 = QtWidgets.QFrame(); sep2.setFrameShape(QtWidgets.QFrame.HLine)
        root.addWidget(sep2)
        root.addWidget(self._section("Step 3 — Choose destination"))

        gds_txt = (
            f"Merge into GDS document  ({self._gds_doc_name})"
            if self._gds_doc_name else "Merge into GDS document  (none open)"
        )
        self._radio_merge = QtWidgets.QRadioButton(gds_txt)
        self._radio_merge.setEnabled(bool(self._gds_doc_name))
        self._radio_keep  = QtWidgets.QRadioButton("Keep model in this document")

        if self._gds_doc_name:
            self._radio_merge.setChecked(True)
        else:
            self._radio_keep.setChecked(True)

        root.addWidget(self._radio_merge)
        root.addWidget(self._radio_keep)
        root.addStretch()

    @staticmethod
    def _section(text: str) -> QtWidgets.QLabel:
        return QtWidgets.QLabel(f"<b>{text}</b>")

    def getStandardButtons(self):
        return qenum_int(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)

    # ── picking mode toggle ───────────────────────────────────────────────────

    def _on_contact_btn_toggled(self, checked: bool):
        if checked:
            self._pick_mode = self._MODE_CONTACT
            self._pick_contact_btn.setText("Picking… click a face on the model")
        else:
            self._pick_mode = self._MODE_TOP
            self._pick_contact_btn.setText("Pick Die-Attach Face")

    def _clear_contact_face(self):
        self._restore_contact_highlight()
        self._contact_obj_name = None
        self._contact_face_idx = None
        self._contact_face_z   = None
        self._contact_lbl.setText("<i>No die-attach face selected.</i>")
        self._clear_contact_btn.setEnabled(False)

    # ── unified face-selection callback ──────────────────────────────────────

    def _on_face_selected(self, obj, sub_name: str, face):
        if self._pick_mode == self._MODE_CONTACT:
            self._receive_contact_face(obj, sub_name, face)
        else:
            self._receive_top_face(obj, sub_name, face)

    # ── top-face branch ───────────────────────────────────────────────────────

    def _receive_top_face(self, obj, sub_name: str, face):
        self._restore_highlight()

        self._sel_obj  = obj
        self._sel_sub  = sub_name
        self._sel_face = face
        try:
            pr = face.ParameterRange
            u  = (pr[0] + pr[1]) / 2.0
            v  = (pr[2] + pr[3]) / 2.0
            self._sel_normal = face.normalAt(u, v)
        except Exception:
            try:
                self._sel_normal = face.normalAt(0.0, 0.0)
            except Exception:
                self._sel_normal = FreeCAD.Vector(0.0, 0.0, 1.0)

        n = self._sel_normal
        self._face_lbl.setText(
            f"<b>Face:</b> {sub_name}  ({obj.Label})<br>"
            f"<b>Normal:</b> ({n.x:.4f},&nbsp;{n.y:.4f},&nbsp;{n.z:.4f})"
        )
        self._apply_btn.setEnabled(True)
        self._rotation_done = False
        self._apply_btn.setText("Apply Rotation")
        self._rot_lbl.setText("")
        self._highlight_face(obj, sub_name)

    def _highlight_face(self, obj, sub_name: str):
        """Highlight top-face selection in orange."""
        try:
            gui_doc = FreeCADGui.getDocument(self._pkg_doc_name)
            if not gui_doc:
                return
            vobj = gui_doc.getObject(obj.Name)
            n    = len(obj.Shape.Faces)
            dc   = list(getattr(vobj, "DiffuseColor", []))
            if len(dc) != n:
                base = dc[0] if dc else (0.8, 0.8, 0.8, 1.0)
                dc   = [base] * n
            self._orig_diffuse = list(dc)
            idx = int(sub_name[4:]) - 1
            if 0 <= idx < n:
                dc[idx] = _COLOR_FACE_PICK
            vobj.DiffuseColor = dc
        except Exception as exc:
            FreeCAD.Console.PrintWarning(f"[PackageOrientation] top highlight: {exc}\n")

    def _restore_highlight(self):
        try:
            if self._sel_obj and self._orig_diffuse is not None:
                gui_doc = FreeCADGui.getDocument(self._pkg_doc_name)
                if gui_doc:
                    vobj = gui_doc.getObject(self._sel_obj.Name)
                    if vobj and hasattr(vobj, "DiffuseColor"):
                        vobj.DiffuseColor = self._orig_diffuse
        except Exception:
            pass

    # ── contact-face branch ───────────────────────────────────────────────────

    def _receive_contact_face(self, obj, sub_name: str, face):
        """Store the die-attach face and exit contact-picking mode."""
        self._restore_contact_highlight()

        idx = int(sub_name[4:]) - 1
        self._contact_obj_name = obj.Name
        self._contact_face_idx = idx

        # World-space Z of the face centre (BoundBox is world-space after recompute).
        # Use the face's own CenterOfMass which matches the BoundBox coordinate system.
        self._contact_face_z = face.CenterOfMass.z

        self._contact_lbl.setText(
            f"<b>Face:</b> {sub_name}  ({obj.Label})<br>"
            f"<b>Z position:</b> {self._contact_face_z:.4f} mm  "
            f"<i>(world space in package doc)</i>"
        )

        # Highlight in blue
        self._highlight_contact_face(obj, sub_name)

        self._clear_contact_btn.setEnabled(True)

        # Auto-exit contact-picking mode
        self._pick_contact_btn.setChecked(False)   # triggers _on_contact_btn_toggled

        FreeCAD.Console.PrintMessage(
            f"[PackageOrientation] Die-attach face: {sub_name} on '{obj.Label}', "
            f"Z = {self._contact_face_z:.4f} mm\n"
        )

        # Automatically close the panel and merge once the die-attach face is picked.
        QtCore.QTimer.singleShot(100, self._auto_accept)

    def _auto_accept(self):
        """Called automatically after the die-attach face is picked; merges and closes."""
        self.accept()
        FreeCADGui.Control.closeDialog()

    def _highlight_contact_face(self, obj, sub_name: str):
        """Highlight contact-face selection in blue."""
        try:
            gui_doc = FreeCADGui.getDocument(self._pkg_doc_name)
            if not gui_doc:
                return
            vobj = gui_doc.getObject(obj.Name)
            n    = len(obj.Shape.Faces)
            dc   = list(getattr(vobj, "DiffuseColor", []))
            if len(dc) != n:
                base = dc[0] if dc else (0.8, 0.8, 0.8, 1.0)
                dc   = [base] * n
            self._contact_diffuse = list(dc)
            idx = int(sub_name[4:]) - 1
            if 0 <= idx < n:
                dc[idx] = _COLOR_CONTACT_FACE
            vobj.DiffuseColor = dc
        except Exception as exc:
            FreeCAD.Console.PrintWarning(f"[PackageOrientation] contact highlight: {exc}\n")

    def _restore_contact_highlight(self):
        try:
            if self._contact_obj_name and self._contact_diffuse is not None:
                pkg_doc = FreeCAD.getDocument(self._pkg_doc_name)
                gui_doc = FreeCADGui.getDocument(self._pkg_doc_name)
                if pkg_doc and gui_doc:
                    obj  = pkg_doc.getObject(self._contact_obj_name)
                    vobj = gui_doc.getObject(self._contact_obj_name) if obj else None
                    if vobj and hasattr(vobj, "DiffuseColor"):
                        vobj.DiffuseColor = self._contact_diffuse
        except Exception:
            pass

    # ── rotation ──────────────────────────────────────────────────────────────

    def _apply_rotation(self):
        if self._sel_normal is None:
            return
        doc = FreeCAD.getDocument(self._pkg_doc_name)
        if not doc:
            return

        rot = _compute_rotation_to_z(self._sel_normal)
        _rotate_pkg_doc(doc, rot)

        n = self._sel_normal
        self._rotation_done = True
        self._orig_diffuse  = None      # shape changed; old colour data invalid
        self._apply_btn.setText("Rotation Applied ✓")
        self._apply_btn.setEnabled(False)
        self._rot_lbl.setText(
            f"Rotated:  ({n.x:.3f}, {n.y:.3f}, {n.z:.3f})  →  Z+"
        )

        # Enable die-attach face picking now that rotation is finalised
        self._pick_contact_btn.setEnabled(True)

        try:
            FreeCADGui.setActiveDocument(self._pkg_doc_name)
            FreeCADGui.activeDocument().activeView().fitAll()
        except Exception:
            pass

    # ── ok / cancel ───────────────────────────────────────────────────────────

    def accept(self):
        self._cleanup()
        if self._radio_merge.isChecked() and self._gds_doc_name:
            self._merge_into_gds()
        else:
            self._keep_in_pkg_doc()

    def reject(self):
        self._cleanup()
        try:
            FreeCAD.closeDocument(self._pkg_doc_name)
            FreeCAD.Console.PrintMessage(
                "[PackageOrientation] Cancelled — package document discarded.\n"
            )
        except Exception:
            pass

    def _cleanup(self):
        try:
            FreeCADGui.Selection.removeObserver(self._observer)
        except Exception:
            pass
        self._restore_highlight()
        self._restore_contact_highlight()

    # ── destination ───────────────────────────────────────────────────────────

    def _merge_into_gds(self):
        try:
            pkg_doc = FreeCAD.getDocument(self._pkg_doc_name)
        except Exception:
            pkg_doc = None
        try:
            gds_doc = FreeCAD.getDocument(self._gds_doc_name)
        except Exception:
            gds_doc = None
        if not pkg_doc or not gds_doc:
            FreeCAD.Console.PrintError(
                f"[PackageOrientation] Cannot merge: "
                f"pkg_doc='{self._pkg_doc_name}' found={pkg_doc is not None}, "
                f"gds_doc='{self._gds_doc_name}' found={gds_doc is not None}.\n"
            )
            return

        # XY target = centre of existing GDS geometry
        gds_bb = _doc_combined_bbox(gds_doc)
        if gds_bb:
            target_cx = (gds_bb.XMin + gds_bb.XMax) / 2.0
            target_cy = (gds_bb.YMin + gds_bb.YMax) / 2.0
            gds_zmin  = gds_bb.ZMin
        else:
            target_cx = target_cy = gds_zmin = 0.0

        pkg_bb = _doc_combined_bbox(pkg_doc)
        if pkg_bb:
            pkg_cx   = (pkg_bb.XMin + pkg_bb.XMax) / 2.0
            pkg_cy   = (pkg_bb.YMin + pkg_bb.YMax) / 2.0
            pkg_zmin = pkg_bb.ZMin
        else:
            pkg_cx = pkg_cy = pkg_zmin = 0.0

        # ── Z alignment ───────────────────────────────────────────────────────
        # If a die-attach face was chosen: align it with the GDS bottom layer.
        # The contact face Z was recorded in the pkg_doc world space after
        # rotation; gds_zmin is the bottom of all GDS layer geometry.
        # We want:  contact_face_z + dz  =  gds_zmin
        #           → dz = gds_zmin - contact_face_z
        #
        # Fallback (no contact face): seat the package bottom at Z = 0.
        if self._contact_face_z is not None:
            dz = gds_zmin - self._contact_face_z
            FreeCAD.Console.PrintMessage(
                f"[PackageOrientation] Die-attach alignment: "
                f"contact face Z = {self._contact_face_z:.4f} mm  →  "
                f"GDS ZMin = {gds_zmin:.4f} mm  (dz = {dz:.4f} mm)\n"
            )
        else:
            dz = -pkg_zmin          # seat package bottom at Z = 0
            FreeCAD.Console.PrintMessage(
                f"[PackageOrientation] No die-attach face — "
                f"seating package bottom at Z = 0  (dz = {dz:.4f} mm)\n"
            )

        offset = FreeCAD.Vector(target_cx - pkg_cx, target_cy - pkg_cy, dz)

        copied = 0
        for obj in pkg_doc.Objects:
            try:
                if not (hasattr(obj, "Shape") and obj.Shape and not obj.Shape.isNull()):
                    continue
                new_obj = gds_doc.addObject("Part::Feature", obj.Label)
                new_obj.Shape = obj.Shape.copy()
                pl = obj.Placement.copy()
                pl.Base = pl.Base + offset
                new_obj.Placement = pl
                new_obj.Label = obj.Label
                try:
                    new_obj.ViewObject.ShapeColor = obj.ViewObject.ShapeColor
                    new_obj.ViewObject.LineColor  = obj.ViewObject.LineColor
                except Exception:
                    pass
                copied += 1
            except Exception as exc:
                FreeCAD.Console.PrintWarning(
                    f"[PackageOrientation] skipping '{obj.Label}': {exc}\n"
                )

        gds_doc.recompute()
        FreeCAD.Console.PrintMessage(
            f"[PackageOrientation] {copied} object(s) merged into "
            f"'{self._gds_doc_name}', "
            f"XY centre ({target_cx:.3f}, {target_cy:.3f}) mm.\n"
        )

        try:
            FreeCADGui.setActiveDocument(self._gds_doc_name)
            view = FreeCADGui.activeDocument().activeView()
            view.viewIsometric()
            view.fitAll()
        except Exception:
            pass

        try:
            FreeCAD.closeDocument(self._pkg_doc_name)
        except Exception:
            pass

    def _keep_in_pkg_doc(self):
        try:
            FreeCADGui.setActiveDocument(self._pkg_doc_name)
            view = FreeCADGui.activeDocument().activeView()
            view.viewIsometric()
            view.fitAll()
        except Exception:
            pass
        FreeCAD.Console.PrintMessage(
            f"[PackageOrientation] Package kept in '{self._pkg_doc_name}'.\n"
        )


# ---------------------------------------------------------------------------
# STEP / IGES import into a dedicated package document
# ---------------------------------------------------------------------------

def _import_into_freecad(file_path: str):
    """
    Import *file_path* into a brand-new FreeCAD document, then launch the
    interactive _PackageOrientationPanel so the user can:

      1. Click a face to define the top (Z+) surface.
      2. Apply the rotation that aligns that face normal with Z+.
      3. Choose to merge the result into an existing GDS document, or keep
         the package model in its own document.
    """
    entry_name   = os.path.splitext(os.path.basename(file_path))[0]
    pkg_label    = f"Pkg_{entry_name}"

    # Always create a fresh, dedicated document for this package.
    pkg_doc      = FreeCAD.newDocument(pkg_label)
    pkg_doc_name = pkg_doc.Name      # FreeCAD may append a number on collision

    docs_before  = set(FreeCAD.listDocuments().keys())
    ImportGui.insert(file_path, pkg_doc_name)

    # Some importers open a new document instead of inserting into the named one.
    # Detect stray documents and copy their shapes into pkg_doc.
    docs_after    = set(FreeCAD.listDocuments().keys())
    stray_names   = docs_after - docs_before - {pkg_doc_name}
    for nd_name in stray_names:
        try:
            nd = FreeCAD.getDocument(nd_name)
            for obj in list(nd.Objects):
                if hasattr(obj, "Shape") and obj.Shape and not obj.Shape.isNull():
                    new_obj       = pkg_doc.addObject("Part::Feature", obj.Label)
                    new_obj.Shape = obj.Shape.copy()
                    new_obj.Label = obj.Label
            FreeCAD.closeDocument(nd_name)
            FreeCAD.Console.PrintMessage(
                f"[LeadframeLibrary] Merged stray document '{nd_name}' "
                f"into '{pkg_doc_name}'.\n"
            )
        except Exception as exc:
            FreeCAD.Console.PrintWarning(
                f"[LeadframeLibrary] Could not merge '{nd_name}': {exc}\n"
            )

    pkg_doc = FreeCAD.getDocument(pkg_doc_name)
    pkg_doc.recompute()

    for obj in pkg_doc.Objects:
        try:
            obj.ViewObject.Visibility = True
        except Exception:
            pass

    try:
        FreeCADGui.setActiveDocument(pkg_doc_name)
        view = FreeCADGui.activeDocument().activeView()
        view.viewIsometric()
        view.fitAll()
    except Exception:
        pass

    # Find any open GDS document so the panel can offer the merge option.
    gds_doc_name = _find_gds_document()

    # Open the orientation task-panel (non-blocking).
    if FreeCADGui.Control.activeDialog():
        FreeCADGui.Control.closeDialog()
    panel = _PackageOrientationPanel(pkg_doc_name, gds_doc_name)
    FreeCADGui.Control.showDialog(panel)


# ---------------------------------------------------------------------------
# Background workers
# ---------------------------------------------------------------------------

class _FetchWorker(QtCore.QThread):
    finished = QtCore.Signal(list)
    failed = QtCore.Signal(str)

    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self._url = url

    def run(self):
        try:
            self.finished.emit(fetch_leadframe_entries(self._url))
        except Exception as exc:
            self.failed.emit(str(exc))


class _PreviewWorker(QtCore.QThread):
    """
    Fetches the package detail page in one request, then:
    - downloads the best product photo  → emits loaded(bytes)
    - extracts textual spec info        → emits text_loaded(str)
    On any error emits failed(str).
    """
    loaded      = QtCore.Signal(bytes)
    text_loaded = QtCore.Signal(str)
    failed      = QtCore.Signal(str)

    def __init__(self, entry: LeadframeEntry, parent=None):
        super().__init__(parent)
        self._entry = entry

    def run(self):
        try:
            if not self._entry.package_page_url:
                self.failed.emit("No package detail page available.")
                return

            photos, info_text = _fetch_package_detail(self._entry.package_page_url)

            # Always emit text info (even when no photo was found)
            if info_text:
                self.text_loaded.emit(info_text)

            if not photos:
                self.failed.emit("No product photos found on the package page.")
                return

            # Prefer a "Top" view; fall back to the first photo found
            preferred = next(
                (p for p in photos if "_top_" in p.lower()),
                photos[0],
            )

            with urllib.request.urlopen(preferred, timeout=10) as resp:
                data = resp.read()
            self.loaded.emit(data)
        except Exception as exc:
            self.failed.emit(str(exc))


class _DownloadWorker(QtCore.QThread):
    finished = QtCore.Signal(str)
    failed = QtCore.Signal(str)

    def __init__(self, entry: LeadframeEntry, parent=None):
        super().__init__(parent)
        self._entry = entry

    def run(self):
        try:
            self.finished.emit(_download_to_temp(self._entry))
        except Exception as exc:
            self.failed.emit(str(exc))


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class LeadframeLibraryDialog(QtWidgets.QDialog):
    """Dialog that shows online leadframes with product photo previews."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Leadframe Online Library")
        self.resize(900, 620)

        self.entries: List[LeadframeEntry] = []
        self._all_entries: List[LeadframeEntry] = []
        self._preview_cache: dict = {}   # package_page_url → QPixmap (or None)
        self._text_cache: dict = {}      # package_page_url → str (or "")
        self._preview_worker: Optional[_PreviewWorker] = None
        self._fetch_worker: Optional[_FetchWorker] = None
        self._download_worker: Optional[_DownloadWorker] = None

        # --- filter row ---
        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setPlaceholderText("Search by name…")
        self.search_edit.textChanged.connect(self._apply_filter)

        self.format_combo = QtWidgets.QComboBox()
        for label in FORMAT_GROUPS:
            self.format_combo.addItem(label)
        self.format_combo.currentIndexChanged.connect(self._apply_filter)

        filter_layout = QtWidgets.QHBoxLayout()
        filter_layout.addWidget(QtWidgets.QLabel("Filter:"))
        filter_layout.addWidget(self.format_combo)
        filter_layout.addWidget(self.search_edit)

        # --- list ---
        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.currentItemChanged.connect(self._handle_selection)
        self.list_widget.itemDoubleClicked.connect(self._import_selected)

        left_layout = QtWidgets.QVBoxLayout()
        left_layout.addLayout(filter_layout)
        left_layout.addWidget(self.list_widget)

        # --- preview pane ---
        self.preview_label = QtWidgets.QLabel("Select an entry to preview.")
        self.preview_label.setAlignment(QtCore.Qt.AlignCenter)
        self.preview_label.setMinimumSize(320, 200)
        self.preview_label.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self.preview_label.setScaledContents(False)

        # Textual info extracted from the package detail page
        self._info_browser = QtWidgets.QTextBrowser()
        self._info_browser.setReadOnly(True)
        self._info_browser.setMinimumHeight(120)
        self._info_browser.setMaximumHeight(200)
        self._info_browser.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self._info_browser.setPlaceholderText("Package information will appear here.")
        self._info_browser.setOpenExternalLinks(False)

        self.status_label = QtWidgets.QLabel("Fetching leadframe library…")
        self.status_label.setWordWrap(True)

        refresh_button = QtWidgets.QPushButton("Refresh")
        refresh_button.clicked.connect(self.refresh_entries)

        self.import_button = QtWidgets.QPushButton("Download && Import")
        self.import_button.clicked.connect(self._import_selected)
        self.import_button.setEnabled(False)

        button_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        button_box.rejected.connect(self.reject)

        side_layout = QtWidgets.QVBoxLayout()
        side_layout.addWidget(self.preview_label)
        side_layout.addWidget(self._info_browser)
        side_layout.addWidget(self.status_label)
        side_layout.addStretch()
        side_layout.addWidget(self.import_button)
        side_layout.addWidget(refresh_button)
        side_layout.addWidget(button_box)

        main_layout = QtWidgets.QHBoxLayout(self)
        main_layout.addLayout(left_layout, 2)
        main_layout.addLayout(side_layout, 3)

        self.refresh_entries()

    # ------------------------------------------------------------------
    # Fetch catalog
    # ------------------------------------------------------------------

    def refresh_entries(self):
        self.status_label.setText("Downloading catalog…")
        self.import_button.setEnabled(False)
        self.list_widget.clear()
        self._all_entries = []
        self.entries = []
        self._preview_cache.clear()
        self._text_cache.clear()
        self._info_browser.clear()

        self._fetch_worker = _FetchWorker(DEFAULT_LIBRARY_URL, parent=self)
        self._fetch_worker.finished.connect(self._on_fetch_done)
        self._fetch_worker.failed.connect(self._on_fetch_failed)
        self._fetch_worker.start()

    def _on_fetch_done(self, entries: list):
        self._all_entries = entries
        self._apply_filter()
        if not entries:
            self.status_label.setText("No downloadable leadframes found on the library page.")
        else:
            self.status_label.setText(f"Found {len(entries)} leadframe resources.")
            if self.list_widget.count():
                self.list_widget.setCurrentRow(0)

    def _on_fetch_failed(self, msg: str):
        self.status_label.setText("Failed to fetch library data. Check your internet connection.")
        QtWidgets.QMessageBox.critical(self, "Download failed", msg)

    # ------------------------------------------------------------------
    # Filter
    # ------------------------------------------------------------------

    def _apply_filter(self):
        text = self.search_edit.text().lower()
        label = self.format_combo.currentText()
        allowed_exts = FORMAT_GROUPS.get(label)

        self.list_widget.clear()
        self.entries = []
        for entry in self._all_entries:
            ext = os.path.splitext(entry.name.lower())[1]
            if allowed_exts is not None and ext not in allowed_exts:
                continue
            if text and text not in entry.name.lower():
                continue
            item = QtWidgets.QListWidgetItem(entry.name)
            item.setData(QtCore.Qt.UserRole, entry)
            self.list_widget.addItem(item)
            self.entries.append(entry)

        count = self.list_widget.count()
        total = len(self._all_entries)
        if count < total:
            self.status_label.setText(f"Showing {count} of {total} resources.")
        elif total:
            self.status_label.setText(f"Found {total} leadframe resources.")

        if count:
            self.list_widget.setCurrentRow(0)
        else:
            self.preview_label.setText("No matching entries.")
            self.preview_label.setPixmap(QtGui.QPixmap())
            self.import_button.setEnabled(False)

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------

    def _handle_selection(self, current, _previous):
        if not current:
            self.preview_label.setText("Select an entry to preview.")
            self.preview_label.setPixmap(QtGui.QPixmap())
            self._info_browser.clear()
            self.import_button.setEnabled(False)
            return
        entry: LeadframeEntry = current.data(QtCore.Qt.UserRole)
        self.import_button.setEnabled(True)
        self._show_preview(entry)

    def _show_preview(self, entry: LeadframeEntry):
        pkg_url = entry.package_page_url
        if not pkg_url:
            self.preview_label.setPixmap(QtGui.QPixmap())
            self.preview_label.setText("No package detail page available.")
            self._info_browser.clear()
            return

        # Serve from cache if available
        if pkg_url in self._preview_cache:
            cached = self._preview_cache[pkg_url]
            if cached is not None:
                self._display_pixmap(cached)
            else:
                self.preview_label.setPixmap(QtGui.QPixmap())
                self.preview_label.setText("No product photos available.")
            # Restore cached text (may be empty string when not yet fetched)
            cached_text = self._text_cache.get(pkg_url, "")
            self._info_browser.setPlainText(cached_text)
            return

        self.preview_label.setText("Loading preview…")
        self.preview_label.setPixmap(QtGui.QPixmap())
        self._info_browser.clear()

        # Cancel previous in-flight request
        if self._preview_worker and self._preview_worker.isRunning():
            try:
                self._preview_worker.loaded.disconnect()
                self._preview_worker.text_loaded.disconnect()
                self._preview_worker.failed.disconnect()
            except Exception:
                pass

        self._preview_worker = _PreviewWorker(entry, parent=self)
        self._preview_worker.loaded.connect(
            lambda data, url=pkg_url: self._on_preview_loaded(data, url)
        )
        self._preview_worker.text_loaded.connect(
            lambda text, url=pkg_url: self._on_text_loaded(text, url)
        )
        self._preview_worker.failed.connect(
            lambda msg, url=pkg_url: self._on_preview_failed(msg, url)
        )
        self._preview_worker.start()

    def _on_preview_loaded(self, data: bytes, pkg_url: str):
        pixmap = QtGui.QPixmap()
        if pixmap.loadFromData(data):
            self._preview_cache[pkg_url] = pixmap
            # Only display if the selection hasn't changed
            current = self.list_widget.currentItem()
            if current:
                entry: LeadframeEntry = current.data(QtCore.Qt.UserRole)
                if entry.package_page_url == pkg_url:
                    self._display_pixmap(pixmap)
        else:
            self._preview_cache[pkg_url] = None
            self.preview_label.setText("Unable to decode preview image.")

    def _on_text_loaded(self, text: str, pkg_url: str):
        """Cache and display package textual info for *pkg_url*."""
        self._text_cache[pkg_url] = text
        current = self.list_widget.currentItem()
        if current:
            entry: LeadframeEntry = current.data(QtCore.Qt.UserRole)
            if entry.package_page_url == pkg_url:
                self._info_browser.setPlainText(text)

    def _on_preview_failed(self, msg: str, pkg_url: str):
        self._preview_cache[pkg_url] = None
        current = self.list_widget.currentItem()
        if current:
            entry: LeadframeEntry = current.data(QtCore.Qt.UserRole)
            if entry.package_page_url == pkg_url:
                self.preview_label.setPixmap(QtGui.QPixmap())
                self.preview_label.setText("No product photos available.")

    def _display_pixmap(self, pixmap: QtGui.QPixmap):
        scaled = pixmap.scaled(
            self.preview_label.size(),
            QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation,
        )
        self.preview_label.setPixmap(scaled)
        self.preview_label.setText("")

    # ------------------------------------------------------------------
    # Import
    # ------------------------------------------------------------------

    def _import_selected(self):
        current_item = self.list_widget.currentItem()
        if not current_item:
            QtWidgets.QMessageBox.information(self, "No selection", "Please select a leadframe entry first.")
            return
        entry: LeadframeEntry = current_item.data(QtCore.Qt.UserRole)

        self.import_button.setEnabled(False)
        self.status_label.setText(f"Downloading '{entry.name}'…")
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)

        self._download_worker = _DownloadWorker(entry, parent=self)
        self._download_worker.finished.connect(lambda path: self._on_download_done(path, entry))
        self._download_worker.failed.connect(self._on_download_failed)
        self._download_worker.start()

    def _on_download_done(self, local_path: str, entry: LeadframeEntry):
        QtWidgets.QApplication.restoreOverrideCursor()
        try:
            self.status_label.setText(f"Importing '{entry.name}'…")
            QtWidgets.QApplication.processEvents()
            _import_into_freecad(local_path)
            # _import_into_freecad launched the orientation task-panel.
            # Close the library dialog so the panel has the user's full focus.
            self.accept()
        except Exception as exc:
            self.status_label.setText("Import failed.")
            self.import_button.setEnabled(True)
            QtWidgets.QMessageBox.critical(self, "Import failed", str(exc))

    def _on_download_failed(self, msg: str):
        QtWidgets.QApplication.restoreOverrideCursor()
        self.status_label.setText("Download failed.")
        self.import_button.setEnabled(True)
        QtWidgets.QMessageBox.critical(self, "Download failed", msg)


def open_leadframe_library():
    dialog = LeadframeLibraryDialog()
    dialog.exec_()


class LeadframeLibraryCommand:
    def GetResources(self):
        return {
            "MenuText": "Leadframe Online Library",
            "ToolTip": "Browse MirrorSemi leadframes, preview, and import them",
            "Pixmap": get_icon("Leadframe_Library.svg"),
        }

    def Activated(self):
        open_leadframe_library()

    def IsActive(self):
        return True


FreeCADGui.addCommand("LeadframeLibraryCommand", LeadframeLibraryCommand())
