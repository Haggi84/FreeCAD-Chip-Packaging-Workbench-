import FreeCAD
import gdstk
from PySide2 import QtWidgets
import Part
from FreeCAD import Vector
import os


def load_gdsii(file_path):
    lib = gdstk.read_gds(file_path)
    return lib

def parse_lyp(filepath):
    layers = {}
    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if line.startswith('LAYER'):
                parts = line.split()
                if len(parts) >= 3:
                    try:
                        layer_id = int(parts[1])
                        layer_name = parts[2]
                        layers[layer_id] = layer_name
                    except:
                        pass
    return layers

class LayerSelector(QtWidgets.QWidget):
    def __init__(self, layer_dict, gds_file):
        super().__init__()
        self.layer_dict = layer_dict
        self.gds_file = gds_file

        self.combo = QtWidgets.QComboBox()
        for lid, lname in sorted(layer_dict.items()):
            self.combo.addItem(f"{lname} ({lid})", lid)

        self.load_btn = QtWidgets.QPushButton("Layer anzeigen")
        self.load_btn.clicked.connect(self.load_layer)

        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(self.combo)
        layout.addWidget(self.load_btn)
        self.setLayout(layout)

    def load_layer(self):
        layer_id = self.combo.currentData()
        display_layer(self.gds_file, layer_id)

def display_layer(file_path, target_layer):
    try:
        doc = FreeCAD.activeDocument()
        if not doc:
            doc = FreeCAD.newDocument("GDSII_Document")

        lib = gdstk.read_gds(file_path)
        for cell in lib.cells:
            for polygon in cell.polygons:
                if polygon.layer == target_layer:
                    add_polygon_to_doc(doc, polygon.points)
        doc.recompute()
        print(f"Layer {target_layer} angezeigt.")
    except Exception as e:
        print("Fehler beim Anzeigen des layer:", e)

def add_polygon_to_doc(doc, points):
    # Punkte polygon schließen falls offen
    if points[0] != points[-1]:
        points = list(points) + [points[0]]

    wire = Part.makePolygon([Vector(p[0], p[1], 0) for p in points])
    obj = doc.addObject("Part::Feature", "GDS_Polygon")
    obj.Shape = wire

def run(gds_file, lyp_file):
    layers = parse_lyp(lyp_file)
    print("Gefundene Layer aus LYP:")
    for lid, lname in layers.items():
        print(f"{lid}: {lname}")

    # GUI starten
    selector = LayerSelector(layers, gds_file)
    selector.setWindowTitle("Layer Auswahl")
    selector.show()
    return selector
