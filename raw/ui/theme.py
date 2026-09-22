"""Dark retro-technical theme.

Restrained: grid-based, high-contrast waveform, modern usability. The look is
retro; the interaction is not (spec section 66).
"""

from __future__ import annotations

COLORS = {
    "bg": "#14161a",
    "panel": "#1b1e24",
    "panel_alt": "#22262e",
    "border": "#2e343e",
    "text": "#d7dce4",
    "text_dim": "#7d8695",
    "accent": "#4fd1a5",
    "accent_dim": "#2c8b6d",
    "warn": "#e0b341",
    "error": "#e06a5c",
    "wave": "#4fd1a5",
    "wave_dim": "#2c8b6d",
    "playhead": "#e0b341",
    "grid": "#262b34",
    "region_attack": "#e06a5c",
    "region_transient": "#e0b341",
    "region_body": "#4fd1a5",
    "region_tail": "#5c9ce0",
    "region_custom": "#a37de0",
}

REGION_COLORS = {
    "attack": COLORS["region_attack"],
    "transient": COLORS["region_transient"],
    "body": COLORS["region_body"],
    "tail": COLORS["region_tail"],
    "custom": COLORS["region_custom"],
}

STYLESHEET = f"""
QWidget {{
    background: {COLORS['bg']};
    color: {COLORS['text']};
    font-size: 12px;
}}
QMainWindow::separator {{ background: {COLORS['border']}; width: 3px; height: 3px; }}
QDockWidget {{ titlebar-close-icon: none; font-weight: 600; }}
QDockWidget::title {{
    background: {COLORS['panel_alt']};
    padding: 6px 8px;
    border-bottom: 1px solid {COLORS['border']};
    letter-spacing: 1px;
}}
QGroupBox {{
    border: 1px solid {COLORS['border']};
    border-radius: 3px;
    margin-top: 14px;
    padding-top: 8px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
    color: {COLORS['text_dim']};
}}
QPushButton {{
    background: {COLORS['panel_alt']};
    border: 1px solid {COLORS['border']};
    border-radius: 3px;
    padding: 5px 12px;
}}
QPushButton:hover {{ border-color: {COLORS['accent_dim']}; }}
QPushButton:pressed {{ background: {COLORS['accent_dim']}; }}
QPushButton:disabled {{ color: {COLORS['text_dim']}; border-color: {COLORS['panel_alt']}; }}
QPushButton#primary {{
    background: {COLORS['accent_dim']};
    border-color: {COLORS['accent']};
    font-weight: 600;
}}
QPushButton#primary:hover {{ background: {COLORS['accent']}; color: {COLORS['bg']}; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {COLORS['panel']};
    border: 1px solid {COLORS['border']};
    border-radius: 3px;
    padding: 3px 6px;
    selection-background-color: {COLORS['accent_dim']};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border-color: {COLORS['accent']};
}}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled,
QLabel:disabled, QGroupBox:disabled, QSlider:disabled, QListWidget:disabled {{
    color: {COLORS['text_dim']};
    background: {COLORS['bg']};
    border-color: {COLORS['panel_alt']};
}}
QSlider::handle:horizontal:disabled {{ background: {COLORS['border']}; }}
QSlider::sub-page:horizontal:disabled {{ background: {COLORS['panel_alt']}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{
    background: {COLORS['panel']};
    border: 1px solid {COLORS['border']};
    selection-background-color: {COLORS['accent_dim']};
}}
QTreeWidget, QTableWidget, QListWidget {{
    background: {COLORS['panel']};
    border: 1px solid {COLORS['border']};
    alternate-background-color: {COLORS['panel_alt']};
}}
QTreeWidget::item, QTableWidget::item {{ padding: 3px; }}
QTreeWidget::item:selected, QTableWidget::item:selected {{
    background: {COLORS['accent_dim']};
    color: #ffffff;
}}
QHeaderView::section {{
    background: {COLORS['panel_alt']};
    border: none;
    border-bottom: 1px solid {COLORS['border']};
    padding: 4px;
    color: {COLORS['text_dim']};
}}
QMenuBar {{ background: {COLORS['panel_alt']}; }}
QMenuBar::item:selected {{ background: {COLORS['accent_dim']}; }}
QMenu {{ background: {COLORS['panel']}; border: 1px solid {COLORS['border']}; }}
QMenu::item:selected {{ background: {COLORS['accent_dim']}; }}
QMenu::item:disabled {{ color: {COLORS['text_dim']}; }}
QStatusBar {{ background: {COLORS['panel_alt']}; border-top: 1px solid {COLORS['border']}; }}
QStatusBar QLabel {{ color: {COLORS['text_dim']}; padding: 0 8px; }}
QSlider::groove:horizontal {{ background: {COLORS['panel']}; height: 4px; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: {COLORS['accent']};
    width: 12px; margin: -5px 0; border-radius: 6px;
}}
QSlider::sub-page:horizontal {{ background: {COLORS['accent_dim']}; border-radius: 2px; }}
QTabBar::tab {{
    background: {COLORS['panel']};
    border: 1px solid {COLORS['border']};
    padding: 5px 12px;
    margin-right: 2px;
}}
QTabBar::tab:selected {{ background: {COLORS['panel_alt']}; border-bottom-color: {COLORS['accent']}; }}
QScrollBar:vertical {{ background: {COLORS['bg']}; width: 11px; }}
QScrollBar:horizontal {{ background: {COLORS['bg']}; height: 11px; }}
QScrollBar::handle {{ background: {COLORS['border']}; border-radius: 5px; min-height: 24px; }}
QScrollBar::handle:hover {{ background: {COLORS['accent_dim']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QToolTip {{
    background: {COLORS['panel_alt']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['accent_dim']};
    padding: 4px;
}}
QCheckBox::indicator {{
    width: 13px; height: 13px;
    border: 1px solid {COLORS['border']};
    border-radius: 2px;
    background: {COLORS['panel']};
}}
QCheckBox::indicator:checked {{ background: {COLORS['accent']}; }}
QProgressBar {{
    border: 1px solid {COLORS['border']};
    border-radius: 3px;
    text-align: center;
    background: {COLORS['panel']};
}}
QProgressBar::chunk {{ background: {COLORS['accent_dim']}; }}
"""
