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
        unique_colors = set()

        # Iterate through the XML elements to find layer properties
        for prop in root.findall(".//properties"):
            layer_dict = {}
            for child in prop:
                layer_dict[child.tag] = child.text if child.text else None

            name = layer_dict.get("name", "Unknown Layer")
            source = layer_dict.get("source", None)
            visible = layer_dict.get("visible", "false") == "true"
            frame_color = layer_dict.get("frame-color", "#000000")  # Default to black if not found
            fill_color = layer_dict.get("fill-color", "#FFFFFF")  # Default to white if not found
            
            if visible and source:
                # Extract layer_id and datatype from the source (e.g., "40/0")
                try:
                    layer_id, datatype = map(int, source.split("/"))
                    layer_dict["layer_id"] = layer_id
                    layer_dict["datatype"] = datatype
                    layers.append(layer_dict)
                    unique_colors.add((frame_color, fill_color))
                except (ValueError, TypeError):
                    FreeCAD.Console.PrintWarning(f"Invalid source format in layer {name}: {source}\n")
                    continue

        return (layers, unique_colors)
    
    except ET.ParseError:
        FreeCAD.Console.PrintError(f"Error parsing LYP file {lyp_path}: Invalid format\n")
        return ([], set())
    except FileNotFoundError:
        FreeCAD.Console.PrintError(f"LYP file {lyp_path} not found\n")
        return ([], set())
    except Exception as e:
        FreeCAD.Console.PrintError(f"An error occurred while parsing LYP file {lyp_path}: {str(e)}\n")
        return ([], set())
    
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

        return layer_set
    
    except Exception as e:
        FreeCAD.Console.PrintError(f"Error reading GDSII file {gds_path}: {str(e)}\n")
        return set()

def load_gds(gds_path, selected_layers):
    """
    Load GDS file and return shapes for selected layers.
    selected_layers is a list of dictionaries containing layer_id, datatype, frame-color, and fill-color.
    """
    try:
        lib = gdstk.read_gds(gds_path)
        shapes = []

        # Create a set of (layer_id, datatype) for selected layers
        layer_set = {(layer.get("layer_id", 0), layer.get("datatype", 0)) for layer in selected_layers}

        for cell in lib.cells:
            for polygon in cell.polygons:
                if (polygon.layer, polygon.datatype) in layer_set:
                    # Convert polygon to FreeCAD shape
                    points = [(p[0], p[1], 0) for p in polygon.points]
                    if len(points) > 2:
                        wire = Part.makePolygon(points)
                        face = Part.Face(wire)
                        extrude_shape = face.extrude(FreeCAD.Vector(0, 0, 3))
                        # Find the corresponding layer for colors
                        for layer in selected_layers:
                            if (layer.get("layer_id", 0), layer.get("datatype", 0)) == (polygon.layer, polygon.datatype):
                                frame_color = layer.get("frame-color", "#000000")
                                fill_color = layer.get("fill-color", "#FFFFFF")
                                shapes.append((extrude_shape, frame_color, fill_color))
                                break

        return shapes
        
    except Exception as e:
        FreeCAD.Console.PrintError(f"Error loading GDS file {gds_path}: {str(e)}\n")
        return []