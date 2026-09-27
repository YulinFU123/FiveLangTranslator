import asyncio
from time import monotonic
from app.providers.base import ASRProvider,TranslationProvider
from app.core.models import RecognitionResult,TranslationResult,SubtitleStatus
class MockASR(ASRProvider):
    provider_id="mock_asr"
    async def initialize(self):return None
    async def recognize_stream(self,s):
        steps=[("I don't",SubtitleStatus.DRAFT),("I don't think he",SubtitleStatus.DRAFT),("I don't think he knows",SubtitleStatus.STABLE),("I don't think he knows the truth.",SubtitleStatus.FINAL)]
        for rev,(text,status) in enumerate(steps,1):
            await asyncio.sleep(.62)
            yield RecognitionResult(s.segment_id,rev,text,"en",.93,s.start_ms,s.end_ms,status,self.provider_id,s.source)
class MockTranslation(TranslationProvider):
    provider_id="mock_translation"
    async def initialize(self):return None
    async def translate(self,r):
        t=monotonic();await asyncio.sleep(.16)
        m={"I don't":"我不……","I don't think he":"我觉得他并不……","I don't think he knows":"我觉得他并不知道……","I don't think he knows the truth.":"我觉得他并不知道真相。"}
        return TranslationResult(r.segment_id,r.revision,r.text,m.get(r.text,r.text),r.source_language,r.target_language,r.status,self.provider_id,int((monotonic()-t)*1000))
