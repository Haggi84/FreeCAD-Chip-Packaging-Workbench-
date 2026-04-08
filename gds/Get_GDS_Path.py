from PySide2 import QtWidgets
import os, sys

root_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root_path)

from core import Core_Functionality

def get_gds_path():
    """
    Prompt user to select GDS file (required), LYP file (optional), and MAP file (optional).
    Returns:
        gds_path: str or None
        lyp_path: str or None
        layers: list of dict
        unique_colors: set of tuples
        ihp_map: dict
        map_path: str or None
    """
    try:
        # Step 1: Select GDS file (REQUIRED)
        gds_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            None, 
            "Select GDS File", 
            "", 
            "GDS Files (*.gds *.GDS)"
        )
        
        if not gds_path or not os.path.exists(gds_path):
            QtWidgets.QMessageBox.critical(None, "Error", "GDS file not found or invalid path.")
            return None, None, None, None, None, None

        # Step 2: Ask if user wants to select LYP file (OPTIONAL)
        reply = QtWidgets.QMessageBox.question(
            None,
            "Layer Properties File (Optional)",
            "Do you have a LYP (Layer Properties) file?\n\n"
            "• LYP files provide layer styling and colors\n"
            "• If you don't have one, default colors will be used\n\n"
            "Select LYP file?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.Yes
        )
        
        lyp_path = None
        layers = []
        unique_colors = set()
        
        if reply == QtWidgets.QMessageBox.Yes:
            lyp_path, _ = QtWidgets.QFileDialog.getOpenFileName(
                None, 
                "Select LYP File (Optional)", 
                "", 
                "LYP Files (*.lyp *.LYP)"
            )
            
            if lyp_path and os.path.exists(lyp_path):
                layers, unique_colors = Core_Functionality.parse_lyp(lyp_path)
                if not layers:
                    QtWidgets.QMessageBox.warning(None, "Warning", "No valid layers found in LYP file.")
                    lyp_path = None
            else:
                QtWidgets.QMessageBox.critical(None, "Error", "LYP file not found or invalid path.")
                lyp_path = None
        else:
            QtWidgets.QMessageBox.information(None, "Information", "LYP file skipped. Using default layer list.")

        # Step 3: Ask if user wants to select MAP file (OPTIONAL)
        reply = QtWidgets.QMessageBox.question(
            None,
            "Technology Map File (Optional)",
            "Do you have a MAP (Technology Mapping) file?\n\n"
            "• MAP files provide technology-specific layer information\n"
            "• If you don't have one, generic layer info will be used\n\n"
            "Select MAP file?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No  # Default to No for MAP
        )
        
        map_path = None
        ihp_map = {}
        
        if reply == QtWidgets.QMessageBox.Yes:
            map_path, _ = QtWidgets.QFileDialog.getOpenFileName(
                None, 
                "Select MAP File (Optional)", 
                "", 
                "MAP Files (*.map *.MAP)"
            )
            
            if map_path and os.path.exists(map_path):
                ihp_map = Core_Functionality.parse_map(map_path)
                if not ihp_map:
                    QtWidgets.QMessageBox.warning(None, "Warning", "No valid mapping found in MAP file.")
                    map_path = None
            else:
                QtWidgets.QMessageBox.critical(None, "Error", "MAP file not found or invalid path.")
                map_path = None
        else:
            QtWidgets.QMessageBox.information(None, "Information", "MAP file skipped. Using generic layer information.")

        return gds_path, lyp_path, layers, unique_colors, map_path, ihp_map
    
    except Exception as e:
        QtWidgets.QMessageBox.critical(None, "Error", f"An error occurred while selecting files: {str(e)}")
        return None, None, None, None, None, None