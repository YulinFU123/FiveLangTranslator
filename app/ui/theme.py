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


DARK_TOKENS = {
    "text": "#e9eef7",
    "window": "#0b1020",
    "card": "#121c31",
    "card_border": "#243653",
    "title": "#f6f8fc",
    "muted": "#91a3bd",
    "button": "#1b2a47",
    "button_border": "#304766",
    "button_hover": "#24395e",
    "primary": "#3b82f6",
    "primary_border": "#60a5fa",
    "primary_hover": "#2563eb",
    "primary_text": "#ffffff",
    "field": "#0d1729",
    "field_border": "#2c4160",
    "tab": "#10182b",
    "tab_selected": "#1e3357",
    "tab_text": "#91a3bd",
    "bar": "#0d1526",
    "accent": "#6ee7b7",
    "warn": "#fbbf24",
    "danger": "#f87171",
}

LIGHT_TOKENS = {
    "text": "#1f2937",
    "window": "#f4f6fb",
    "card": "#ffffff",
    "card_border": "#dbe3ef",
    "title": "#0f172a",
    "muted": "#5b6b82",
    "button": "#eef2f9",
    "button_border": "#cbd6e6",
    "button_hover": "#e2e9f5",
    "primary": "#2563eb",
    "primary_border": "#1d4ed8",
    "primary_hover": "#1e40af",
    "primary_text": "#ffffff",
    "field": "#ffffff",
    "field_border": "#c7d2e2",
    "tab": "#e8edf6",
    "tab_selected": "#ffffff",
    "tab_text": "#5b6b82",
    "bar": "#e8edf6",
    "accent": "#059669",
    "warn": "#b45309",
    "danger": "#b91c1c",
}

TEMPLATE = """
QWidget{font-family:'Microsoft YaHei UI','Segoe UI';color:%(text)s}
QMainWindow{background:%(window)s}
QFrame#card{background:%(card)s;border:1px solid %(card_border)s;border-radius:16px}
QFrame#metric{background:%(card)s;border:1px solid %(card_border)s;border-radius:12px}
QLabel#title{font-size:26px;font-weight:700;color:%(title)s}
QLabel#muted{color:%(muted)s;font-size:13px}
QLabel#metricValue{font-size:20px;font-weight:700;color:%(title)s}
QPushButton{background:%(button)s;border:1px solid %(button_border)s;border-radius:10px;padding:9px 14px;font-weight:600}
QPushButton:hover{background:%(button_hover)s}
QPushButton#primary{background:%(primary)s;border-color:%(primary_border)s;color:%(primary_text)s}
QPushButton#primary:hover{background:%(primary_hover)s}
QPushButton#dirty{background:%(warn)s;border-color:%(warn)s;color:#111827}
QPushButton:disabled{color:%(muted)s}
QPushButton#anchorButton{background:%(button)s;border:1px solid %(button_border)s;border-radius:8px;min-width:34px;min-height:34px}
QPushButton#anchorButton:hover{background:%(button_hover)s}
QPushButton#anchorButton:checked{background:%(primary)s;border-color:%(primary_border)s}
QComboBox,QSpinBox,QLineEdit,QListWidget,QTableWidget{background:%(field)s;border:1px solid %(field_border)s;border-radius:8px;padding:7px}
QProgressBar{background:%(field)s;border:1px solid %(field_border)s;border-radius:8px;padding:2px;min-height:14px;text-align:center;color:%(text)s}
QProgressBar::chunk{background:%(primary)s;border-radius:6px}
QTabWidget::pane{border:0}
QTabBar::tab{background:%(tab)s;padding:10px 18px;margin-right:4px;border-radius:8px;color:%(tab_text)s}
QTabBar::tab:selected{background:%(tab_selected)s;color:%(text)s;font-weight:600}
QStatusBar{background:%(bar)s;color:%(muted)s}
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
