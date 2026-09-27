import numpy as np
from app.audio.models import AudioDevice, AudioSourceKind, DeviceSelectionMode
from app.audio.device_monitor import DevicePolicy, DeviceSnapshot
from app.audio.processing import FixedFrameBuffer, dbfs

def device(idx,name,default=False):
    return AudioDevice(idx,name,AudioSourceKind.SYSTEM_LOOPBACK,48000,2,default,True,"WASAPI")

def test_frame_buffer_preserves_samples():
    buffer=FixedFrameBuffer(512)
    assert list(buffer.push(np.zeros(300,dtype=np.float32)))==[]
    frames=list(buffer.push(np.zeros(724,dtype=np.float32)))
    assert len(frames)==2 and buffer.buffer.size==0

def test_silence_dbfs():
    assert dbfs(np.zeros(512,dtype=np.float32)) <= -99

def test_identity_survives_index_change():
    before=device(2,"Speakers (Loopback)")
    after=device(17,"Speakers (Loopback)")
    assert DeviceSnapshot.build([after]).find(before.identity).device_id==17

def test_follow_default_selects_default():
    first=device(2,"Headset")
    default=device(7,"Speakers",True)
    policy=DevicePolicy(mode=DeviceSelectionMode.FOLLOW_DEFAULT)
    assert policy.select(DeviceSnapshot.build([first,default])).device_id==7

from app.audio.models import NativeAudioPacket, AudioFrame
from app.audio.processing import StreamingLinearResampler, SpeechSegmenter


def test_streaming_resampler_preserves_long_term_rate():
    resampler = StreamingLinearResampler(16000)
    total = 0
    for index in range(100):
        samples = (np.sin(np.arange(480) * 0.05) * 12000).astype(np.int16)
        packet = NativeAudioPacket(samples.tobytes(), 48000, 1, 2, index * 10)
        total += resampler.convert(packet).size
    assert abs(total - 16000) <= 2


def test_segmenter_emits_draft_then_final():
    segmenter = SpeechSegmenter(
        min_speech_ms=64,
        min_silence_ms=64,
        pre_ms=32,
        post_ms=32,
        draft_interval_ms=64,
    )
    drafts = []
    final = None
    probabilities = [0.9, 0.9, 0.9, 0.9, 0.9, 0.1, 0.1]
    for index, probability in enumerate(probabilities):
        frame = AudioFrame(
            np.zeros(512, dtype=np.float32), 16000,
            index * 32, (index + 1) * 32, -30.0, probability,
        )
        _, draft, maybe_final = segmenter.process(
            frame, AudioSourceKind.MICROPHONE, 3
        )
        if draft:
            drafts.append(draft)
        if maybe_final:
            final = maybe_final
    assert drafts
    assert drafts[0].is_final is False
    assert final is not None and final.is_final is True
    assert final.generation == 3
