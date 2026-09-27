from app.core.arbiter import ResultArbiter
from app.core.models import RecognitionResult,SubtitleStatus,SourceType
def make(rev,status):return RecognitionResult("s",rev,"x","en",.9,0,1,status,"mock",SourceType.SYSTEM_AUDIO)
def test_old_revision_rejected():
    a=ResultArbiter();assert a.accept_recognition(make(5,SubtitleStatus.DRAFT));assert not a.accept_recognition(make(3,SubtitleStatus.FINAL))
def test_draft_cannot_replace_final():
    a=ResultArbiter();assert a.accept_recognition(make(10,SubtitleStatus.FINAL));assert not a.accept_recognition(make(11,SubtitleStatus.DRAFT))
