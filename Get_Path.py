"""
Simple helper functions for resource paths
"""
import os
import FreeCAD


def get_root():
    """Get the root directory of the workbench"""
    return os.path.dirname(os.path.abspath(__file__))


def get_icon(name):
    """
    Get icon path from resources/icons folder
    Works from any subfolder
    
    Args:
        name: Icon filename (e.g., "Load GDS.png")
    
    Returns:
        Full path to icon file
    """
    icon_path = os.path.join(get_root(), "resources", "icons", name)
    
    if os.path.exists(icon_path):
        return icon_path
    else:
        FreeCAD.Console.PrintWarning(f"Icon not found: {icon_path}\n")
        return ""


def get_html(name):
    """
    Get HTML file path from resources/html folder
    
    Args:
        name: HTML filename (e.g., "overview.html")
    
    Returns:
        Full path to HTML file
    """
    html_path = os.path.join(get_root(), "resources", "html", name)
    
    if os.path.exists(html_path):
        return html_path
    else:
        FreeCAD.Console.PrintWarning(f"HTML file not found: {html_path}\n")
        return ""


def get_resource(resource_type, name):
    """
    Get any resource file path
    
    Args:
        resource_type: Subfolder in resources (e.g., "icons", "html")
        name: Filename
    
    Returns:
        Full path to resource file
    """
    resource_path = os.path.join(get_root(), "resources", resource_type, name)
    
    if os.path.exists(resource_path):
        return resource_path
    else:
        FreeCAD.Console.PrintWarning(f"Resource not found: {resource_path}\n")
        return ""