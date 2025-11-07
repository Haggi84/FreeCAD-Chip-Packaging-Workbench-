from PySide2 import QtWidgets, QtCore, QtGui
import FreeCAD, FreeCADGui
import os, sys


# Add root to path
root_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root_path)
from Get_Path import get_icon, get_html

class HelpGuideDialog(QtWidgets.QDialog):
    """Dialog to display the workbench help guide."""
    
    def __init__(self, parent=None):
        super(HelpGuideDialog, self).__init__(parent)
        self.setWindowTitle("DI-PASSIONATE FreeCAD Workbench - Help Guide")
        self.setMinimumSize(900, 700)
        
        # Main layout
        layout = QtWidgets.QVBoxLayout(self)
        
        # Title
        title_label = QtWidgets.QLabel("DI-PASSIONATE FreeCAD Workbench Help Guide")
        title_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #2c3e50; margin: 10px;")
        layout.addWidget(title_label)
        
        # Tab widget for different sections
        self.tab_widget = QtWidgets.QTabWidget()
        
        # Create tabs
        self.create_tab("■ Overview", "overview.html")
        self.create_tab("▶ Quick Start", "quickstart.html")
        self.create_tab("⚙ Tools", "tools.html")
        self.create_tab("↯ Workflows", "workflows.html")
        self.create_tab("⚠ Troubleshooting", "troubleshooting.html")
        
        layout.addWidget(self.tab_widget)
        
        # Close button
        close_button = QtWidgets.QPushButton("Close")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)
    
    def create_tab(self, tab_name, html_file):
        """Create a tab with content from HTML file."""
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        
        text_edit = QtWidgets.QTextEdit()
        text_edit.setReadOnly(True)
        
        # Load HTML content
        html_content = self.load_html_content(html_file)
        text_edit.setHtml(html_content)
        
        layout.addWidget(text_edit)
        self.tab_widget.addTab(tab, tab_name)
    
    def load_html_content(self, html_file):
        """
        Load HTML content from file using get_html() function.
        This is the simplified version that uses get_html().
        """
        # Get file path using get_html() function
        file_path = get_html(html_file)
        
        try:
            if file_path:
                with open(file_path, 'r', encoding='utf-8') as f:
                    return f.read()
            else:
                # File not found - show error message
                return f"""
                <h2>Content Not Found</h2>
                <p>HTML file '<strong>{html_file}</strong>' not found in resources/html/ folder.</p>
                <p>Expected location: resources/html/{html_file}</p>
                <p>Please ensure the HTML files are properly installed.</p>
                """
                
        except Exception as e:
            # Error reading file - show error message
            return f"""
            <h2>Error Loading Content</h2>
            <p>Could not load '<strong>{html_file}</strong>'</p>
            <p>Error: {str(e)}</p>
            """


class HelpGuideCommand:
    """Command to show the help guide dialog."""
    
    def GetResources(self):
        return {
            "MenuText": "Help Guide",
            "ToolTip": "Show comprehensive help guide for DI-PASSIONATE workbench",
            "Pixmap": get_icon("Help_Guide.png")
        }

    def Activated(self):
        """Execute when Help Guide command is clicked"""
        try:
            # Create and show the help dialog
            dialog = HelpGuideDialog(FreeCADGui.getMainWindow())
            dialog.exec_()
            
        except Exception as e:
            # Fallback to simple message if dialog fails
            QtWidgets.QMessageBox.information(
                FreeCADGui.getMainWindow(),
                "Help Guide",
                "DI-PASSIONATE FreeCAD Workbench Help\n\n"
                "Core Features:\n"
                "• GDSII Import with LYP styling\n"
                "• Leadframe Design (QFN/QFP/BGA)\n"
                "• Housing Design with transparency\n"
                "• Layer Stacking on leadframes\n"
                "• Manual 2D Wire Bonding\n\n"
                "HTML help files not found or error occurred."
            )

    def IsActive(self):
        return True

FreeCADGui.addCommand('HelpGuideCommand', HelpGuideCommand())