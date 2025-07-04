import xml.etree.ElementTree as ET
import gdstk
import FreeCAD
import Part


def parse_lyp(lyp_path):

    """
    Parse a LYP file and return a list of tuples (layer_name, layer_id, datatype).

    only includes visible layers.
    """

    try:
        tree = ET.parse(lyp_path)
        root = tree.getroot()
        layers = []

        # Iterate through the XML elements to find layer properties
        for layer in root.findall(".//properties"):
            name = layer.find("name").text if layer.find("name") is not None else "Unknown"
            source = layer.find("source").text if layer.find("source") is not None else None
            visible = layer.find("visible").text == "true" if layer.find("visible") is not None else False
            
    
            if visible and source:
                # Extract layer_id and datatype from the source (e.g.,  "40/0")
                try:
                    layer_id, _ = map(int, source.split("/"))
                    layers.append((name, layer_id))
                except (ValueError, TypeError):
                    FreeCAD.Console.PrintWarning(f"Invalid source format in layer {name}: {source}\n")
                    continue

       
        return layers
    
    except ET.ParseError:
            FreeCAD.Console.PrintError(f"Error parsing LYP file {lyp_path}: Invalid format\n")
            return []
    except FileNotFoundError:
            FreeCAD.Console.PrintError(f"LYP file {lyp_path} not found\n")
            return []
    except Exception as e:
            FreeCAD.Console.PrintError(f"An error occurred while parsing LYP file {lyp_path}: {str(e)}\n")
            return []
    
def get_gds_layer(gds_path):
    """
    Analyze GDS file and return a set of (layer_id, datatype) pairs with geometries.
    """
    try:
        lib = gdstk.read_gds(gds_path)
        layer_set = set()

        for cell in lib.cells:
            for polygon in cell.polygons:
                layer_set.add((polygon.layer, polygon.datatype))

        # Return a set of unique layer IDs
        unique_layers_ids = set(layer[0] for layer in layer_set)

        return unique_layers_ids
    
    except Exception as e:
        FreeCAD.Console.PrintError(f"Error reading GDSII file {gds_path}: {str(e)}\n")
        return set()



def load_gds(gds_path, selected_layers):
    """
    Load GDS file and return shapes for selected layers.
    selected_layers is a list of tuples (layer_name, layer_id).
    """
    try:
        lib = gdstk.read_gds(gds_path)
        shapes = []

        # Convert selected layers to a set of (layer IDs, datatypes) for filtering
        selected_layer_set = set()
        for layer_name, layer_id in selected_layers:
            selected_layer_set.add((layer_id, 0))  # Assuming datatype is always 0 for simplicity
        

        for cell in lib.cells:
            for polygon in cell.polygons:
                if (polygon.layer, polygon.datatype) in selected_layer_set:

                    # Convert polygon to FreeCAD shape
                    points = [FreeCAD.Vector(p[0], p[1], 0) for p in polygon.points]

                    if len(points) > 2:
                        wire = Part.makePolygon(points)
                        face = Part.Face(wire)

                        # Extrude the face by 3mm in the Z direction
                        extrude_shape = face.extrude(FreeCAD.Vector(0, 0, 1))
                        shapes.append(extrude_shape)

        return shapes
        
    except Exception as e:
        FreeCAD.Console.PrintError(f"Error loading GDS file {gds_path}: {str(e)}\n")
        return []
