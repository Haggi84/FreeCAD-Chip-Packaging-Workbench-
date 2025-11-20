"""Online leadframe library browser for MirrorSemi CAD resources."""

import os
import re
import tempfile
import urllib.request
from typing import Dict, List, Optional
from urllib.parse import urljoin

import FreeCAD
import FreeCADGui
import ImportGui
from PySide2 import QtCore, QtGui, QtWidgets

from Get_Path import get_icon

DEFAULT_LIBRARY_URL = "https://www.mirrorsemi.com/CAD.html"
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123 Safari/537.36",  # noqa: E501
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.google.com/",
}
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


class LeadframeEntry:
    """Simple data container for a leadframe library entry."""

    def __init__(self, name: str, url: str, preview_url: Optional[str] = None):
        self.name = name
        self.url = url
        self.preview_url = preview_url

    def __repr__(self) -> str:  # pragma: no cover - convenience only
        return f"LeadframeEntry(name={self.name}, url={self.url}, preview={self.preview_url})"


def _find_images(html: str, base_url: str) -> List[str]:
    image_matches = re.findall(r"<img[^>]+src=['\"]([^'\"]+)['\"]", html, flags=re.IGNORECASE)
    return [urljoin(base_url, src) for src in image_matches]


def _find_detail_preview(base_name: str, library_url: str) -> Optional[str]:
    """Try to load the detail HTML page for a part and pull the first matching image."""

    if not base_name:
        return None
    detail_url = urljoin(library_url, f"{base_name}.html")
    request = urllib.request.Request(detail_url, headers=DEFAULT_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            html_bytes = response.read()
    except Exception:
        return None

    html = html_bytes.decode("utf-8", errors="ignore")
    for img in _find_images(html, detail_url):
        if base_name.lower() in img.lower():
            return img
    images = _find_images(html, detail_url)
    return images[0] if images else None


def fetch_leadframe_entries(library_url: str = DEFAULT_LIBRARY_URL) -> List[LeadframeEntry]:
    """Fetch the MirrorSemi CAD page and collect downloadable leadframe entries."""

    request = urllib.request.Request(library_url, headers=DEFAULT_HEADERS)
    with urllib.request.urlopen(request, timeout=15) as response:
        html_bytes = response.read()
    html = html_bytes.decode("utf-8", errors="ignore")

    images = _find_images(html, library_url)
    entries: List[LeadframeEntry] = []

    for match in re.finditer(r"href=['\"]([^'\"]+)['\"]", html, flags=re.IGNORECASE):
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
        if not preview_url:
            preview_url = _find_detail_preview(base_name, library_url)
        entries.append(LeadframeEntry(name=file_name, url=full_url, preview_url=preview_url))

    if not entries and images:
        # If we only found images, surface them as selectable entries.
        for img in images:
            file_name = os.path.basename(img.split("?")[0]) or "leadframe-preview"
            if any(file_name.lower().endswith(ext) for ext in IMAGE_EXTS):
                entries.append(LeadframeEntry(name=file_name, url=img, preview_url=img))

    return entries


def _download_to_temp(entry: LeadframeEntry) -> str:
    target_dir = tempfile.mkdtemp(prefix="leadframe_download_")
    target_name = os.path.basename(entry.url.split("?")[0]) or entry.name
    target_path = os.path.join(target_dir, target_name)
    request = urllib.request.Request(entry.url, headers=DEFAULT_HEADERS)
    with urllib.request.urlopen(request, timeout=30) as response, open(target_path, "wb") as fh:
        fh.write(response.read())
    return target_path


def _import_into_freecad(file_path: str):
    doc = FreeCAD.activeDocument()
    if not doc:
        doc = FreeCAD.newDocument("LeadframeLibrary")
    ImportGui.insert(file_path, doc.Name)
    doc.recompute()
    if FreeCADGui.activeDocument():
        FreeCADGui.activeDocument().activeView().viewIsometric()
        FreeCADGui.SendMsgToActiveView("ViewFit")


class LeadframeLibraryDialog(QtWidgets.QDialog):
    """Dialog that shows online leadframes with optional previews."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Leadframe Online Library")
        self.resize(800, 500)

        self.entries: List[LeadframeEntry] = []
        self.preview_cache: Dict[str, QtGui.QPixmap] = {}
        self.current_preview: Optional[QtGui.QPixmap] = None

        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.currentItemChanged.connect(self._handle_selection)
        self.list_widget.itemDoubleClicked.connect(self._import_selected)

        filter_label = QtWidgets.QLabel("Filter by type:")
        self.filter_combo = QtWidgets.QComboBox()
        self.filter_combo.currentIndexChanged.connect(self._populate_list)

        self.preview_label = QtWidgets.QLabel("Select an entry to preview.")
        self.preview_label.setAlignment(QtCore.Qt.AlignCenter)
        self.preview_label.setMinimumSize(320, 240)
        self.preview_label.setFrameShape(QtWidgets.QFrame.StyledPanel)

        self.status_label = QtWidgets.QLabel("Fetching leadframe library…")
        self.status_label.setWordWrap(True)

        refresh_button = QtWidgets.QPushButton("Refresh")
        refresh_button.clicked.connect(self.refresh_entries)

        import_button = QtWidgets.QPushButton("Download && Import")
        import_button.clicked.connect(self._import_selected)

        button_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        button_box.rejected.connect(self.reject)

        side_layout = QtWidgets.QVBoxLayout()
        side_layout.addWidget(self.preview_label)
        side_layout.addWidget(self.status_label)
        side_layout.addWidget(filter_label)
        side_layout.addWidget(self.filter_combo)
        side_layout.addStretch()
        side_layout.addWidget(import_button)
        side_layout.addWidget(refresh_button)
        side_layout.addWidget(button_box)

        main_layout = QtWidgets.QHBoxLayout(self)
        main_layout.addWidget(self.list_widget, 2)
        main_layout.addLayout(side_layout, 3)

        self.refresh_entries()

    def refresh_entries(self):
        self.status_label.setText("Downloading catalog…")
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            self.entries = fetch_leadframe_entries()
            self._populate_filter_options()
            self._populate_list()
            if not self.entries:
                self.status_label.setText("No downloadable leadframes were found on the library page.")
            else:
                self.status_label.setText(f"Found {len(self.entries)} leadframe resources.")
        except Exception as exc:
            self.status_label.setText("Failed to fetch library data. Please check your internet connection or proxy settings.")
            QtWidgets.QMessageBox.critical(self, "Download failed", str(exc))
            self.entries = []
            self.list_widget.clear()
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

    def _handle_selection(self, current, _previous):
        if not current:
            self.preview_label.setText("Select an entry to preview.")
            self.preview_label.setPixmap(QtGui.QPixmap())
            self.current_preview = None
            return
        entry: LeadframeEntry = current.data(QtCore.Qt.UserRole)
        self._show_preview(entry)

    def _show_preview(self, entry: LeadframeEntry):
        preview_url = entry.preview_url or entry.url
        if not any(preview_url.lower().endswith(ext) for ext in IMAGE_EXTS):
            self.preview_label.setText("No image preview available for this entry.")
            self.preview_label.setPixmap(QtGui.QPixmap())
            return
        try:
            pixmap = self._get_pixmap(preview_url)
            if pixmap:
                scaled = pixmap.scaled(
                    self.preview_label.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation
                )
                self.preview_label.setPixmap(scaled)
                self.current_preview = pixmap
                self.preview_label.setText("")
                self.status_label.setText(f"Preview loaded from {preview_url}")
            else:
                self.preview_label.setText("Unable to load preview image.")
                self.preview_label.setPixmap(QtGui.QPixmap())
                self.current_preview = None
        except Exception as exc:
            self.preview_label.setText("Preview could not be downloaded.")
            self.preview_label.setPixmap(QtGui.QPixmap())
            self.current_preview = None
            QtWidgets.QMessageBox.warning(self, "Preview error", str(exc))

    def _import_selected(self):
        current_item = self.list_widget.currentItem()
        if not current_item:
            QtWidgets.QMessageBox.information(self, "No selection", "Please select a leadframe entry first.")
            return
        entry: LeadframeEntry = current_item.data(QtCore.Qt.UserRole)
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            local_path = _download_to_temp(entry)
            _import_into_freecad(local_path)
            QtWidgets.QMessageBox.information(
                self,
                "Imported",
                f"Leadframe '{entry.name}' downloaded to:\n{local_path}\nand inserted into the current document.",
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Import failed", str(exc))
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

    def _entry_extension(self, entry: LeadframeEntry) -> str:
        url_path = entry.url.split("?")[0]
        ext = os.path.splitext(url_path)[1].lower()
        if not ext:
            ext = os.path.splitext(entry.name)[1].lower()
        return ext

    def _populate_filter_options(self):
        extensions = sorted({self._entry_extension(entry) for entry in self.entries if self._entry_extension(entry)})
        self.filter_combo.blockSignals(True)
        self.filter_combo.clear()
        self.filter_combo.addItem("All types", "")
        for ext in extensions:
            self.filter_combo.addItem(ext, ext)
        self.filter_combo.blockSignals(False)
        self.filter_combo.setCurrentIndex(0)

    def _populate_list(self):
        selected_ext = self.filter_combo.currentData()
        self.list_widget.clear()
        for entry in self.entries:
            ext = self._entry_extension(entry)
            if selected_ext and ext != selected_ext:
                continue
            item = QtWidgets.QListWidgetItem(entry.name)
            item.setData(QtCore.Qt.UserRole, entry)
            self._set_item_icon(item, entry)
            self.list_widget.addItem(item)
        if self.list_widget.count():
            self.list_widget.setCurrentRow(0)
            self._handle_selection(self.list_widget.currentItem(), None)

    def _get_pixmap(self, url: str) -> Optional[QtGui.QPixmap]:
        if url in self.preview_cache:
            return self.preview_cache[url]
        request = urllib.request.Request(url, headers=DEFAULT_HEADERS)
        with urllib.request.urlopen(request, timeout=10) as response:
            data = response.read()
        pixmap = QtGui.QPixmap()
        if pixmap.loadFromData(data):
            self.preview_cache[url] = pixmap
            return pixmap
        return None

    def _set_item_icon(self, item: QtWidgets.QListWidgetItem, entry: LeadframeEntry):
        preview_url = entry.preview_url or entry.url
        if not any(preview_url.lower().endswith(ext) for ext in IMAGE_EXTS):
            return
        try:
            pixmap = self._get_pixmap(preview_url)
        except Exception:
            return
        if pixmap:
            icon_pixmap = pixmap.scaled(96, 96, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
            item.setIcon(QtGui.QIcon(icon_pixmap))

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        if self.current_preview:
            scaled = self.current_preview.scaled(
                self.preview_label.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation
            )
            self.preview_label.setPixmap(scaled)


def open_leadframe_library():
    dialog = LeadframeLibraryDialog()
    dialog.exec_()


class LeadframeLibraryCommand:
    def GetResources(self):
        return {
            "MenuText": "Leadframe Online Library",
            "ToolTip": "Browse MirrorSemi leadframes, preview, and import them",
            "Pixmap": get_icon("Leadframe_Configurator.png"),
        }

    def Activated(self):
        open_leadframe_library()

    def IsActive(self):
        return True


FreeCADGui.addCommand("LeadframeLibraryCommand", LeadframeLibraryCommand())
