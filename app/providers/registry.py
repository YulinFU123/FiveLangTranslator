class ProviderRegistry:
    def __init__(self):self._items={}
    def register(self,p):
        if p.provider_id in self._items:raise ValueError(p.provider_id)
        self._items[p.provider_id]=p
    def get(self,key):return self._items[key]
    async def initialize_all(self):
        for p in self._items.values():await p.initialize()
