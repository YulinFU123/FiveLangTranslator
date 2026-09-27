from app.hardware.vulkan import probe_vulkan
from pathlib import Path
from app.asr.whisper_cpp import WhisperCppProvider

root = Path(__file__).resolve().parents[1]
exe = WhisperCppProvider.discover(root)
models = sorted((root / "models").glob("*.bin"))
model = models[0] if models else None
probe = WhisperCppProvider.probe(exe, model)
print("available:", probe.available)
print("executable:", probe.executable or "not found")
print("model:", probe.model or "not found")
print("gpu hint:", probe.vulkan_hint)
print("details:", probe.details[:800])

print("vulkan:", probe_vulkan().to_dict())
