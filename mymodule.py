import FreeCAD
import gdstk
import Part
from FreeCAD import Vector
from PySide2 import QtWidgets

def parse_lyp(filepath):
    layers = {}
    with open(filepath, 'r') as f:
        for line in f:
            if line.strip().startswith('LAYER'):
                parts = line.strip().split()
                if len(parts) >= 3:
                    try:
                        layer_id = int(parts[1])
                        layer_name = parts[2]
                        layers[layer_id] = layer_name
                    except:
                        pass
    return layers

def load_gdsii(file_path):
    return gdstk.read_gds(file_path)

def get_gds_layers(gds_lib):
    return sorted({polygon.layer for cell in gds_lib.cells for polygon in cell.polygons})

def create_prism(points, height, z_offset):
    if points[0][0] != points[-1][0] or points[0][1] != points[-1][1]:
        points = list(points) + [points[0]]
    wire = Part.makePolygon([Vector(p[0], p[1], z_offset) for p in points])
    if not wire.isClosed():
        return None
    face = Part.Face(wire)
    return face.extrude(Vector(0, 0, height))

def display_layer(gds_lib, target_layer, height=1.0, z_offset=0.0):
    doc = FreeCAD.activeDocument() or FreeCAD.newDocument("GDSII_3D")
    prisms = []

    for cell in gds_lib.cells:
        for polygon in cell.polygons:
            if polygon.layer == target_layer:
                prism = create_prism(polygon.points, height, z_offset)
                if prism:
                    prisms.append(prism)

    if prisms:
        compound = Part.Compound(prisms)
        obj = doc.addObject("Part::Feature", f"GDS_Layer_{target_layer}")
        obj.Shape = compound
        doc.recompute()

def display_all_layers(gds_lib, gds_layers, height=1.0, start_z=0.0):
    doc = FreeCAD.activeDocument() or FreeCAD.newDocument("GDSII_3D")
    all_compounds = []

    for layer in gds_layers:
        prisms = []
        for cell in gds_lib.cells:
            for polygon in cell.polygons:
                if polygon.layer == layer:
                    prism = create_prism(polygon.points, height, start_z)
                    if prism:
                        prisms.append(prism)
        start_z += height
        if prisms:
            all_compounds.append(Part.Compound(prisms))

    if all_compounds:
        total = Part.Compound(all_compounds)
        obj = doc.addObject("Part::Feature", "GDS_AllLayers")
        obj.Shape = total
        doc.recompute()

class LayerSelector(QtWidgets.QWidget):
    def __init__(self, gds_lib, layer_dict):
        super().__init__()
        self.gds_lib = gds_lib
        self.layer_dict = layer_dict

        self.combo = QtWidgets.QComboBox()
        self.all_layers_btn = QtWidgets.QPushButton("Import All Layers (3D, Fast)")

        gds_layers = get_gds_layers(gds_lib)
        for lid in gds_layers:
            lname = layer_dict.get(lid, "Unknown")
            self.combo.addItem(f"{lname} ({lid})", lid)

        self.load_btn = QtWidgets.QPushButton("Display Selected Layer (3D)")
        self.load_btn.clicked.connect(self.load_layer)
        self.all_layers_btn.clicked.connect(self.load_all_layers)

        self.height_input = QtWidgets.QDoubleSpinBox()
        self.height_input.setRange(0.01, 1000.0)
        self.height_input.setValue(1.0)

        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(QtWidgets.QLabel("Select Layer:"))
        layout.addWidget(self.combo)
        layout.addWidget(QtWidgets.QLabel("Height (um):"))
        layout.addWidget(self.height_input)
        layout.addWidget(self.load_btn)
        layout.addWidget(self.all_layers_btn)
        self.setLayout(layout)

    def load_layer(self):
        layer_id = self.combo.currentData()
        height = self.height_input.value()
        display_layer(self.gds_lib, layer_id, height)

    def load_all_layers(self):
        height = self.height_input.value()
        gds_layers = get_gds_layers(self.gds_lib)
        display_all_layers(self.gds_lib, gds_layers, height)

def run(gds_path, lyp_path):
    gds_lib = load_gdsii(gds_path)
    layer_dict = parse_lyp(lyp_path)
    selector = LayerSelector(gds_lib, layer_dict)
    selector.setWindowTitle("GDSII 3D Layer Selector (Fast)")
    selector.show()
    return selector
