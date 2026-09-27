from dataclasses import dataclass
from app.core.models import SubtitleStatus
@dataclass(slots=True)
class SegmentState:
    recognition:object|None=None; translation:object|None=None
class ResultArbiter:
    def __init__(self): self._segments={}
    def _accept(self,current,incoming):
        if current is None:return True
        if incoming.revision<current.revision:return False
        if current.status==SubtitleStatus.FINAL and incoming.status!=SubtitleStatus.FINAL:return False
        return not (incoming.revision==current.revision and incoming.status<current.status)
    def accept_recognition(self,x):
        s=self._segments.setdefault(x.segment_id,SegmentState())
        if not self._accept(s.recognition,x):return False
        s.recognition=x;return True
    def accept_translation(self,x):
        s=self._segments.setdefault(x.segment_id,SegmentState())
        if not self._accept(s.translation,x):return False
        s.translation=x;return True
    def get(self,key):return self._segments.get(key)
