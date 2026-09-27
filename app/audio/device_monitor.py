from __future__ import annotations
import threading
from dataclasses import dataclass
from app.audio.models import AudioDevice, AudioSourceKind, DeviceIdentity, DeviceSelectionMode

@dataclass(slots=True)
class DeviceSnapshot:
    devices: dict[DeviceIdentity,AudioDevice]
    @classmethod
    def build(cls,items):return cls({x.identity:x for x in items})
    def default(self,kind):
        values=[x for x in self.devices.values() if x.source_kind==kind]
        return next((x for x in values if x.is_default),values[0] if values else None)
    def find(self,identity):
        exact=self.devices.get(identity)
        if exact:return exact
        return next((d for k,d in self.devices.items() if k.name_key==identity.name_key and k.source_kind==identity.source_kind and k.is_loopback==identity.is_loopback),None)

class DevicePolicy:
    def __init__(self,kind=AudioSourceKind.SYSTEM_LOOPBACK,mode=DeviceSelectionMode.FOLLOW_DEFAULT,preferred=None,auto_fallback=True,restore=True):
        self.kind=kind;self.mode=mode;self.preferred=preferred;self.auto_fallback=auto_fallback;self.restore=restore
    def select(self,snapshot):
        if self.mode!=DeviceSelectionMode.FOLLOW_DEFAULT and self.preferred:
            found=snapshot.find(self.preferred)
            if found:return found
            if self.mode==DeviceSelectionMode.SPECIFIC and not self.auto_fallback:return None
        return snapshot.default(self.kind)

class DeviceMonitor:
    def __init__(self,capture,interval=1.0):
        self.capture=capture;self.interval=max(.3,interval);self.snapshot=DeviceSnapshot({});self.on_change=None;self.on_error=None;self.stop_event=threading.Event();self.thread=None
    def start(self):
        if self.thread and self.thread.is_alive():return
        self.stop_event.clear();self._refresh();self.thread=threading.Thread(target=self._loop,name="device-monitor",daemon=True);self.thread.start()
    def stop(self):
        self.stop_event.set()
        if self.thread:self.thread.join(2);self.thread=None
    def _refresh(self):
        newer=DeviceSnapshot.build(self.capture.list_devices());old=self.snapshot;self.snapshot=newer
        old_keys=set(old.devices);new_keys=set(newer.devices)
        changed=old_keys!=new_keys or any(old.devices[k].is_default!=newer.devices[k].is_default for k in old_keys&new_keys)
        if changed and self.on_change:self.on_change(old,newer)
    def _loop(self):
        while not self.stop_event.wait(self.interval):
            try:self._refresh()
            except Exception as exc:
                if self.on_error:self.on_error(exc)
