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
from PySide2 import QtCore, QtGui, QtWidgets

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


def _fetch_package_photos(package_page_url: str) -> List[str]:
    """
    Fetch a per-package detail page and return absolute URLs of product photos.

    Photos are <img> elements whose src contains the package name (as opposed to
    generic site chrome like arrows, icons, etc.).  The package name is derived
    from the page URL stem (e.g. 'M-QFN8W.65' from 'M-QFN8W.65.html').
    """
    pkg_stem = os.path.splitext(os.path.basename(package_page_url.split("?")[0]))[0]

    with urllib.request.urlopen(package_page_url, timeout=15) as resp:
        html = resp.read().decode("utf-8", errors="ignore")

    srcs = re.findall(r'<img[^>]+src="([^"]+)"', html, re.IGNORECASE)
    photos: List[str] = []
    for src in srcs:
        if pkg_stem.lower() in src.lower() and any(src.lower().endswith(e) for e in IMAGE_EXTS):
            photos.append(urljoin(package_page_url, src))
    return photos


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


def _import_into_freecad(file_path: str):
    doc = FreeCAD.activeDocument()
    if not doc:
        doc = FreeCAD.newDocument("LeadframeLibrary")

    objects_before = set(doc.Objects)
    ImportGui.insert(file_path, doc.Name)
    doc.recompute()

    _place_imported_package(doc, objects_before)
    doc.recompute()

    if FreeCADGui.activeDocument():
        FreeCADGui.activeDocument().activeView().viewIsometric()
        FreeCADGui.SendMsgToActiveView("ViewFit")


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
    Fetches the package detail page, picks the best product photo, then
    downloads it.  Emits either loaded(bytes) or failed(str).
    """
    loaded = QtCore.Signal(bytes)
    failed = QtCore.Signal(str)

    def __init__(self, entry: LeadframeEntry, parent=None):
        super().__init__(parent)
        self._entry = entry

    def run(self):
        try:
            if not self._entry.package_page_url:
                self.failed.emit("No package detail page available.")
                return

            photos = _fetch_package_photos(self._entry.package_page_url)
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
        self.resize(860, 540)

        self.entries: List[LeadframeEntry] = []
        self._all_entries: List[LeadframeEntry] = []
        self._preview_cache: dict = {}   # package_page_url → QPixmap (or None)
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
        self.preview_label.setMinimumSize(320, 240)
        self.preview_label.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self.preview_label.setScaledContents(False)

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
            return

        # Serve from cache if available
        if pkg_url in self._preview_cache:
            cached = self._preview_cache[pkg_url]
            if cached is not None:
                self._display_pixmap(cached)
            else:
                self.preview_label.setPixmap(QtGui.QPixmap())
                self.preview_label.setText("No product photos available.")
            return

        self.preview_label.setText("Loading preview…")
        self.preview_label.setPixmap(QtGui.QPixmap())

        # Cancel previous in-flight request
        if self._preview_worker and self._preview_worker.isRunning():
            try:
                self._preview_worker.loaded.disconnect()
                self._preview_worker.failed.disconnect()
            except Exception:
                pass

        self._preview_worker = _PreviewWorker(entry, parent=self)
        self._preview_worker.loaded.connect(
            lambda data, url=pkg_url: self._on_preview_loaded(data, url)
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
            self.status_label.setText(f"Imported '{entry.name}' — centred at origin.")
            QtWidgets.QMessageBox.information(
                self,
                "Imported",
                f"'{entry.name}' was imported and centred at the origin.\n\nLocal copy:\n{local_path}",
            )
        except Exception as exc:
            self.status_label.setText("Import failed.")
            QtWidgets.QMessageBox.critical(self, "Import failed", str(exc))
        finally:
            self.import_button.setEnabled(True)

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
