# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
"""
Parsers for technology-description files used by the IHP SG13G2 PDK and KLayout.

Supported formats:
  - KLayout LYP (layer properties, colors, visibility)
  - IHP .map (EDI layer name + type mapping)
  - KLayout ELayers XML (physical stackup: Z positions and thicknesses)
"""
import xml.etree.ElementTree as ET
import FreeCAD


def parse_lyp(lyp_path, layer_map=None):
    """
    Parse a KLayout LYP file.

    Returns (layers, unique_colors) where:
        layers        — list of dicts with keys name, source, visible,
                        frame-color, fill-color, layer_id, datatype
        unique_colors — set of (frame_color, fill_color) tuples

    Only visible layers with a valid layer/datatype source are returned.
    """
    try:
        tree = ET.parse(lyp_path)
        root = tree.getroot()
        layers       = []
        unique_colors = set()

        for prop in root.findall(".//properties"):
            layer_dict = {child.tag: (child.text or None) for child in prop}

            visible      = layer_dict.get("visible", "false") == "true"
            source       = layer_dict.get("source", None)
            frame_color  = layer_dict.get("frame-color", "#000000")
            fill_color   = layer_dict.get("fill-color",  "#FFFFFF")

            if not (visible and source):
                continue
            try:
                layer_id, datatype = map(int, source.split("/"))
                layer_dict["layer_id"] = layer_id
                layer_dict["datatype"] = datatype
                layers.append(layer_dict)
                unique_colors.add((frame_color, fill_color))
            except (ValueError, TypeError):
                FreeCAD.Console.PrintWarning(
                    f"Invalid source format in layer {source}: {source}\n"
                )

        return layers, unique_colors

    except ET.ParseError:
        FreeCAD.Console.PrintError(f"Error parsing LYP file {lyp_path}: Invalid format\n")
    except FileNotFoundError:
        FreeCAD.Console.PrintError(f"LYP file {lyp_path} not found\n")
    except Exception as e:
        FreeCAD.Console.PrintError(f"An error occurred while parsing LYP file {lyp_path}: {e}\n")
    return [], set()


def parse_map(map_path):
    """
    Parse an IHP *.map file.

    Returns dict keyed by (gds_layer, gds_datatype):
        { 'edi_name': str, 'edi_types': set[str] }

    Multiple lines with the same (layer, datatype) pair are merged (union of types).
    """
    try:
        layer_map = {}
        with open(map_path, "r", encoding="utf-8", errors="ignore") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "====" in line:
                    continue
                if any(kw in line.lower()
                       for kw in ("date", "copyright", "license", "edi stream", "version")):
                    continue
                try:
                    parts = line.split()
                    if len(parts) < 4:
                        continue
                    edi_name       = parts[0]
                    edi_types_csv  = parts[1]
                    gds_layer      = int(parts[2])
                    gds_datatype   = int(parts[3])
                except (ValueError, IndexError):
                    FreeCAD.Console.PrintWarning(
                        f"Invalid line in .map file {map_path}: {line}\n"
                    )
                    continue

                key   = (gds_layer, gds_datatype)
                types = {t.strip().upper() for t in edi_types_csv.split(",") if t.strip()}
                entry = layer_map.setdefault(key, {"edi_name": edi_name, "edi_types": set()})
                entry["edi_name"] = edi_name
                entry["edi_types"].update(types)

        return layer_map

    except FileNotFoundError:
        FreeCAD.Console.PrintError(f"MAP file '{map_path}' not found\n")
    except Exception as e:
        FreeCAD.Console.PrintError(f"Failed to parse MAP file '{map_path}': {e}\n")
    return {}


# Backward-compat alias
parse_ihp_map = parse_map


def parse_stackup_xml(xml_path):
    """
    Parse a KLayout/IHP ELayers stackup XML.

    Returns a lookup dict keyed by both layer name (upper-case str) and GDS
    layer number (int):
        "METAL1" -> { 'zmin_um', 'zmax_um', 'thickness_um', 'gds_layer', 'type' }
        8        -> { same }

    Returns {} on any error so callers can safely fall back to hard-coded defaults.
    """
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        result = {}
        for layer in root.findall(".//Layer"):
            name  = layer.get("Name", "").strip()
            ltype = layer.get("Type", "conductor").lower()
            try:
                zmin_um = float(layer.get("Zmin", 0))
                zmax_um = float(layer.get("Zmax", 0))
            except ValueError:
                continue
            gds_layer_str = layer.get("Layer", "")
            gds_layer     = int(gds_layer_str) if gds_layer_str.isdigit() else -1
            entry = {
                "zmin_um":      zmin_um,
                "zmax_um":      zmax_um,
                "thickness_um": abs(zmax_um - zmin_um),
                "gds_layer":    gds_layer,
                "type":         ltype,
            }
            if name:
                result[name.upper()] = entry
            if gds_layer >= 0:
                result[gds_layer] = entry

        FreeCAD.Console.PrintMessage(
            f"Loaded stackup XML '{xml_path}': "
            f"{sum(isinstance(k, str) for k in result)} layers.\n"
        )
        return result

    except FileNotFoundError:
        FreeCAD.Console.PrintError(f"Stackup XML '{xml_path}' not found.\n")
    except ET.ParseError as e:
        FreeCAD.Console.PrintError(f"Stackup XML parse error in '{xml_path}': {e}\n")
    except Exception as e:
        FreeCAD.Console.PrintError(f"Failed to load stackup XML '{xml_path}': {e}\n")
    return {}
