"""Online leadframe library browser for MirrorSemi CAD resources."""

import os
import re
import tempfile
import threading
import urllib.request
from typing import List, Optional
from urllib.parse import urljoin

import FreeCAD
import FreeCADGui
import ImportGui
import Part
from PySide2 import QtCore, QtGui, QtWidgets

from Get_Path import get_icon

DEFAULT_LIBRARY_URL = "https://www.mirrorsemi.com/CAD.html"

# All extensions the downloader will accept
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

# Format groups shown in the filter combo
FORMAT_GROUPS = {
    "All formats": None,
    "3D models (STEP / IGES)": (".stp", ".step", ".igs", ".iges"),
    "2D drawings (DXF / DWG)": (".dxf", ".dwg"),
    "FreeCAD files": (".fcstd",),
    "Archives (ZIP / RAR / 7Z)": (".zip", ".rar", ".7z"),
}


class LeadframeEntry:
    """Simple data container for a leadframe library entry."""

    def __init__(self, name: str, url: str, preview_url: Optional[str] = None):
        self.name = name
        self.url = url
        self.preview_url = preview_url

    def __repr__(self) -> str:  # pragma: no cover
        return f"LeadframeEntry(name={self.name}, url={self.url}, preview={self.preview_url})"


def _find_images(html: str, base_url: str) -> List[str]:
    image_matches = re.findall(r"<img[^>]+src=\"([^\"]+)\"", html, flags=re.IGNORECASE)
    return [urljoin(base_url, src) for src in image_matches]


def fetch_leadframe_entries(library_url: str = DEFAULT_LIBRARY_URL) -> List[LeadframeEntry]:
    """Fetch the MirrorSemi CAD page and collect downloadable leadframe entries."""

    with urllib.request.urlopen(library_url, timeout=15) as response:
        html_bytes = response.read()
    html = html_bytes.decode("utf-8", errors="ignore")

    images = _find_images(html, library_url)
    entries: List[LeadframeEntry] = []

    for match in re.finditer(r"href=\"([^\"]+)\"", html, flags=re.IGNORECASE):
        href = match.group(1)
        if not href:
            continue
        lower_href = href.lower().split("?")[0]
        if not lower_href.endswith(ACCEPTED_DOWNLOAD_EXTS):
            continue
        full_url = urljoin(library_url, href)
        file_name = os.path.basename(lower_href) or "leadframe"
        base_name, _ = os.path.splitext(file_name)
        preview_url = None
        for img in images:
            if base_name and base_name.lower() in img.lower():
                preview_url = img
                break
        entries.append(LeadframeEntry(name=file_name, url=full_url, preview_url=preview_url))

    if not entries and images:
        for img in images:
            file_name = os.path.basename(img.split("?")[0]) or "leadframe-preview"
            if any(file_name.lower().endswith(ext) for ext in IMAGE_EXTS):
                entries.append(LeadframeEntry(name=file_name, url=img, preview_url=img))

    return entries


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


def _center_at_origin(doc, objects_before):
    """Move newly imported objects so their combined bounding-box centre is at the origin."""
    new_objects = [o for o in doc.Objects if o not in objects_before]
    if not new_objects:
        return

    # Compute combined bounding box over all new objects that have one
    bbox = None
    for obj in new_objects:
        try:
            bb = obj.Shape.BoundBox
            if not bb.isValid():
                continue
            if bbox is None:
                bbox = bb
            else:
                bbox.add(bb)
        except Exception:
            continue

    if bbox is None or not bbox.isValid():
        return

    cx, cy, cz = bbox.Center.x, bbox.Center.y, bbox.Center.z
    if abs(cx) < 1e-6 and abs(cy) < 1e-6 and abs(cz) < 1e-6:
        return  # already at origin

    offset = FreeCAD.Vector(-cx, -cy, -cz)
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

    _center_at_origin(doc, objects_before)
    doc.recompute()

    if FreeCADGui.activeDocument():
        FreeCADGui.activeDocument().activeView().viewIsometric()
        FreeCADGui.SendMsgToActiveView("ViewFit")


# ---------------------------------------------------------------------------
# Background workers
# ---------------------------------------------------------------------------

class _FetchWorker(QtCore.QThread):
    finished = QtCore.Signal(list)   # list[LeadframeEntry]
    failed = QtCore.Signal(str)      # error message

    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self._url = url

    def run(self):
        try:
            entries = fetch_leadframe_entries(self._url)
            self.finished.emit(entries)
        except Exception as exc:
            self.failed.emit(str(exc))


class _PreviewWorker(QtCore.QThread):
    loaded = QtCore.Signal(bytes)   # raw image bytes
    failed = QtCore.Signal(str)

    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self._url = url

    def run(self):
        try:
            with urllib.request.urlopen(self._url, timeout=10) as resp:
                data = resp.read()
            self.loaded.emit(data)
        except Exception as exc:
            self.failed.emit(str(exc))


class _DownloadWorker(QtCore.QThread):
    finished = QtCore.Signal(str)   # local file path
    failed = QtCore.Signal(str)

    def __init__(self, entry: LeadframeEntry, parent=None):
        super().__init__(parent)
        self._entry = entry

    def run(self):
        try:
            path = _download_to_temp(self._entry)
            self.finished.emit(path)
        except Exception as exc:
            self.failed.emit(str(exc))


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class LeadframeLibraryDialog(QtWidgets.QDialog):
    """Dialog that shows online leadframes with optional previews."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Leadframe Online Library")
        self.resize(860, 540)

        self.entries: List[LeadframeEntry] = []
        self._all_entries: List[LeadframeEntry] = []
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
    # Fetch
    # ------------------------------------------------------------------

    def refresh_entries(self):
        self.status_label.setText("Downloading catalog…")
        self.import_button.setEnabled(False)
        self.list_widget.clear()
        self._all_entries = []
        self.entries = []

        url = DEFAULT_LIBRARY_URL
        self._fetch_worker = _FetchWorker(url, parent=self)
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
        allowed_exts = FORMAT_GROUPS.get(label)  # None means all

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
        preview_url = entry.preview_url or entry.url
        if not any(preview_url.lower().split("?")[0].endswith(ext) for ext in IMAGE_EXTS):
            ext = os.path.splitext(entry.name.lower())[1]
            self.preview_label.setPixmap(QtGui.QPixmap())
            self.preview_label.setText(f"No image preview available.\n\nFile type: {ext or 'unknown'}")
            return

        self.preview_label.setText("Loading preview…")
        self.preview_label.setPixmap(QtGui.QPixmap())

        # Cancel previous in-flight preview request
        if self._preview_worker and self._preview_worker.isRunning():
            self._preview_worker.finished.disconnect()
            self._preview_worker.failed.disconnect()

        self._preview_worker = _PreviewWorker(preview_url, parent=self)
        self._preview_worker.loaded.connect(self._on_preview_loaded)
        self._preview_worker.failed.connect(self._on_preview_failed)
        self._preview_worker.start()

    def _on_preview_loaded(self, data: bytes):
        pixmap = QtGui.QPixmap()
        if pixmap.loadFromData(data):
            scaled = pixmap.scaled(
                self.preview_label.size(),
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation,
            )
            self.preview_label.setPixmap(scaled)
            self.preview_label.setText("")
        else:
            self.preview_label.setText("Unable to decode preview image.")

    def _on_preview_failed(self, msg: str):
        self.preview_label.setText("Preview could not be downloaded.")

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
