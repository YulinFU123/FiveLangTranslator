from PySide6.QtCore import QObject,Signal
class EventBus(QObject):
    recognition=Signal(object);translation=Signal(object);subtitle=Signal(object);status=Signal(str);error=Signal(str);topmost_changed=Signal(bool);subtitleVisibilityChanged=Signal(bool);subtitleAnchorChanged=Signal(str)
    subtitleStyleChanged=Signal(object)
