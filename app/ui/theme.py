from __future__ import annotations


def system_prefers_light() -> bool:
    """Windows apps theme. Falls back to dark when the registry is unavailable."""
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        value, _type = winreg.QueryValueEx(key, "AppsUseLightTheme")
        winreg.CloseKey(key)
        return bool(value)
    except Exception:
        return False


# Frosted-glass palette. Surfaces are deliberately translucent so the Windows 11
# Mica/Acrylic backdrop (see window_styles.set_backdrop) shows through, which is
# what gives the Apple-like depth. Solid fallbacks keep it readable on Windows 10.

DARK_TOKENS = {
    "text": "#f2f4f8",
    # A real translucent window relies on the Windows 11 Mica backdrop, which is
    # not available everywhere; when it fails Qt paints the surface black. An
    # opaque gradient keeps the glassy look while never going black.
    "window": "qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #1a1f2e,stop:1 #0b0e16)",
    "card": "rgba(255,255,255,0.062)",
    "card_border": "rgba(255,255,255,0.11)",
    "title": "#ffffff",
    "muted": "rgba(233,239,248,0.56)",
    "button": "rgba(255,255,255,0.085)",
    "button_border": "rgba(255,255,255,0.13)",
    "button_hover": "rgba(255,255,255,0.16)",
    "button_press": "rgba(255,255,255,0.22)",
    "primary": "#0a84ff",
    "primary_border": "#0a84ff",
    "primary_hover": "#3d9bff",
    "primary_text": "#ffffff",
    "field": "rgba(255,255,255,0.07)",
    "field_border": "rgba(255,255,255,0.14)",
    "tab": "transparent",
    "tab_selected": "rgba(255,255,255,0.13)",
    "tab_text": "rgba(233,239,248,0.60)",
    "bar": "rgba(255,255,255,0.045)",
    "accent": "#30d158",
    "warn": "#ffd60a",
    "danger": "#ff453a",
}

LIGHT_TOKENS = {
    "text": "#1c1c1e",
    "window": "qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #eaeef6,stop:1 #f8fafd)",
    "card": "rgba(255,255,255,0.80)",
    "card_border": "rgba(0,0,0,0.07)",
    "title": "#000000",
    "muted": "rgba(60,60,67,0.58)",
    "button": "rgba(255,255,255,0.72)",
    "button_border": "rgba(0,0,0,0.08)",
    "button_hover": "rgba(255,255,255,0.92)",
    "button_press": "rgba(0,0,0,0.06)",
    "primary": "#007aff",
    "primary_border": "#007aff",
    "primary_hover": "#0a6ed1",
    "primary_text": "#ffffff",
    "field": "rgba(255,255,255,0.80)",
    "field_border": "rgba(0,0,0,0.10)",
    "tab": "transparent",
    "tab_selected": "rgba(255,255,255,0.95)",
    "tab_text": "rgba(60,60,67,0.60)",
    "bar": "rgba(255,255,255,0.50)",
    "accent": "#248a3d",
    "warn": "#b25000",
    "danger": "#d70015",
}

TEMPLATE = """
QWidget{
    font-family:'Segoe UI Variable Display','Segoe UI','Microsoft YaHei UI';
    font-size:13px;
    color:%(text)s;
}
QMainWindow{background:%(window)s}
QFrame#card{
    background:%(card)s;
    border:1px solid %(card_border)s;
    border-radius:18px;
}
QFrame#metric{
    background:%(card)s;
    border:1px solid %(card_border)s;
    border-radius:14px;
}
QLabel#title{font-size:27px;font-weight:700;color:%(title)s}
QLabel#muted{color:%(muted)s;font-size:12.5px}
QLabel#metricValue{font-size:21px;font-weight:700;color:%(title)s}

QPushButton{
    background:%(button)s;
    border:1px solid %(button_border)s;
    border-radius:11px;
    padding:9px 16px;
    font-weight:600;
}
QPushButton:hover{background:%(button_hover)s}
QPushButton:pressed{background:%(button_press)s}
QPushButton#primary{
    background:%(primary)s;
    border-color:%(primary_border)s;
    color:%(primary_text)s;
}
QPushButton#primary:hover{background:%(primary_hover)s}
QPushButton#dirty{background:%(warn)s;border-color:%(warn)s;color:#1c1c1e}
QPushButton:disabled{color:%(muted)s;background:%(bar)s}

QPushButton#anchorButton{
    background:%(button)s;
    border:1px solid %(button_border)s;
    border-radius:9px;
    min-width:34px;
    min-height:34px;
}
QPushButton#anchorButton:hover{background:%(button_hover)s}
QPushButton#anchorButton:checked{background:%(primary)s;border-color:%(primary_border)s;color:%(primary_text)s}

QComboBox,QSpinBox,QLineEdit,QListWidget,QTableWidget,QTextEdit{
    background:%(field)s;
    border:1px solid %(field_border)s;
    border-radius:9px;
    padding:7px 9px;
    selection-background-color:%(primary)s;
}
QComboBox:hover,QSpinBox:hover,QLineEdit:hover{border-color:%(primary_border)s}
QComboBox QAbstractItemView{
    background:%(card)s;
    border:1px solid %(card_border)s;
    border-radius:10px;
    padding:4px;
}
QComboBox::drop-down{border:0;width:18px}

QCheckBox::indicator{
    width:16px;height:16px;border-radius:5px;
    border:1px solid %(field_border)s;background:%(field)s;
}
QCheckBox::indicator:checked{background:%(primary)s;border-color:%(primary_border)s}

QProgressBar{
    background:%(field)s;
    border:1px solid %(field_border)s;
    border-radius:9px;
    padding:2px;
    min-height:14px;
    text-align:center;
    color:%(text)s;
}
QProgressBar::chunk{background:%(primary)s;border-radius:7px}

QTabWidget::pane{border:0;background:transparent}
QTabBar::tab{
    background:%(tab)s;
    padding:9px 17px;
    margin-right:3px;
    border-radius:10px;
    color:%(tab_text)s;
    font-weight:500;
}
QTabBar::tab:hover{background:%(button)s}
QTabBar::tab:selected{background:%(tab_selected)s;color:%(text)s;font-weight:600}

QScrollArea{background:transparent;border:0}
QScrollArea>QWidget>QWidget{background:transparent}
QScrollBar:vertical{background:transparent;width:10px;margin:0}
QScrollBar::handle:vertical{background:%(button_hover)s;border-radius:5px;min-height:28px}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0}
QScrollBar:horizontal{background:transparent;height:10px;margin:0}
QScrollBar::handle:horizontal{background:%(button_hover)s;border-radius:5px;min-width:28px}
QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal{width:0}

QStatusBar{background:%(bar)s;color:%(muted)s;border:0}
QToolTip{background:%(card)s;color:%(text)s;border:1px solid %(card_border)s;border-radius:8px;padding:5px 8px}
"""


def build_style(light: bool = False) -> str:
    tokens = LIGHT_TOKENS if light else DARK_TOKENS
    return TEMPLATE % tokens


def tokens_for(light: bool = False) -> dict:
    return dict(LIGHT_TOKENS if light else DARK_TOKENS)


_current_light = False


def set_theme_mode(light: bool) -> None:
    """Remembers the active mode so ad-hoc widgets (toast, chart) can follow it."""
    global _current_light
    _current_light = bool(light)


def current_tokens() -> dict:
    return tokens_for(_current_light)


def current_light() -> bool:
    return _current_light


APP_STYLE = build_style(False)
