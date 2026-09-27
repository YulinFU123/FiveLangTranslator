from abc import ABC,abstractmethod
class ASRProvider(ABC):
    provider_id:str
    @abstractmethod
    async def initialize(self):...
    @abstractmethod
    async def recognize_stream(self,segment):
        if False:yield None
    async def close(self):return None
class TranslationProvider(ABC):
    provider_id:str
    @abstractmethod
    async def initialize(self):...
    @abstractmethod
    async def translate(self,request):...
    async def close(self):return None
