from __future__ import annotations
from abc import ABC, abstractmethod
from collections.abc import Callable
from time import monotonic
from app.audio.models import AudioDevice, AudioSourceKind, NativeAudioPacket

PacketCallback = Callable[[NativeAudioPacket], None]
ErrorCallback = Callable[[Exception], None]

def now_ms() -> int: return int(monotonic() * 1000)

class BaseAudioCapture(ABC):
    @abstractmethod
    def list_devices(self) -> list[AudioDevice]: ...
    @abstractmethod
    def start(self, device_id: int, on_packet: PacketCallback,
              on_error: ErrorCallback | None = None) -> None: ...
    @abstractmethod
    def stop(self) -> None: ...

class PyAudioWASAPICapture(BaseAudioCapture):
    def __init__(self, frames_per_buffer: int = 960):
        self.frames_per_buffer = frames_per_buffer
        self._audio = None; self._stream = None
        self._on_packet = None; self._on_error = None
        self._rate = 48000; self._channels = 2

    @staticmethod
    def _pyaudio():
        try:
            import pyaudiowpatch as pyaudio
            return pyaudio
        except ImportError as exc:
            raise RuntimeError("未安装 PyAudioWPatch，请执行 pip install -e .") from exc

    def list_devices(self) -> list[AudioDevice]:
        pa = self._pyaudio(); result=[]
        with pa.PyAudio() as audio:
            try: default_in=int(audio.get_default_input_device_info()["index"])
            except Exception: default_in=None
            try: default_loop=int(audio.get_default_wasapi_loopback()["index"])
            except Exception: default_loop=None
            for info in audio.get_device_info_generator():
                if int(info.get("maxInputChannels",0)) <= 0: continue
                idx=int(info["index"]); loop=bool(info.get("isLoopbackDevice",False))
                kind=AudioSourceKind.SYSTEM_LOOPBACK if loop else AudioSourceKind.MICROPHONE
                try: host=str(audio.get_host_api_info_by_index(int(info.get("hostApi",0))).get("name",""))
                except Exception: host=""
                result.append(AudioDevice(idx,str(info.get("name","Unknown")),kind,
                    int(float(info.get("defaultSampleRate",48000))),
                    int(info.get("maxInputChannels",1)),
                    idx==(default_loop if loop else default_in),loop,host))
        return result

    def start(self, device_id, on_packet, on_error=None):
        self.stop(); pa=self._pyaudio(); self._audio=pa.PyAudio(); self._on_packet=on_packet; self._on_error=on_error
        info=self._audio.get_device_info_by_index(device_id)
        self._rate=int(float(info.get("defaultSampleRate",48000))); self._channels=max(1,int(info.get("maxInputChannels",1)))
        def callback(data, count, time_info, status):
            try:
                if data: on_packet(NativeAudioPacket(bytes(data),self._rate,self._channels,2,now_ms()))
                if status and on_error: on_error(RuntimeError(f"音频流状态：{status}"))
            except Exception as exc:
                if on_error: on_error(exc)
            return None, pa.paContinue
        self._stream=self._audio.open(format=pa.paInt16,channels=self._channels,rate=self._rate,input=True,
            input_device_index=device_id,frames_per_buffer=self.frames_per_buffer,stream_callback=callback,start=True)

    def stop(self):
        if self._stream is not None:
            try:
                if self._stream.is_active(): self._stream.stop_stream()
            except Exception: pass
            try: self._stream.close()
            except Exception: pass
            self._stream=None
        if self._audio is not None:
            try: self._audio.terminate()
            except Exception: pass
            self._audio=None
