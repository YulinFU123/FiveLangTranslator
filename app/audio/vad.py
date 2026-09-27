from __future__ import annotations
import numpy as np
class EnergyVADEngine:
    name="energy_fallback"
    def __init__(self,threshold_db=-46.0):self.threshold_db=threshold_db
    def initialize(self):return None
    def reset(self):return None
    def process(self,samples,level_db):
        probability=max(0.0,min(1.0,(level_db+64.0)/28.0));return probability
    def close(self):return None
class SileroVADEngine:
    name="silero_onnx"
    def __init__(self,threshold=.55):self.threshold=threshold;self.model=None
    def initialize(self):
        from silero_vad import load_silero_vad
        self.model=load_silero_vad(onnx=True);self.reset()
    def reset(self):
        if self.model and hasattr(self.model,"reset_states"):self.model.reset_states()
    def process(self,samples,level_db):
        import torch
        if samples.size!=512:raise ValueError("Silero VAD requires 512 samples at 16 kHz")
        with torch.inference_mode():return float(self.model(torch.from_numpy(np.ascontiguousarray(samples,dtype=np.float32)),16000).item())
    def close(self):self.reset();self.model=None

def create_vad(prefer_silero=True):
    if prefer_silero:
        try:
            engine=SileroVADEngine();engine.initialize();return engine
        except Exception:pass
    engine=EnergyVADEngine();engine.initialize();return engine
