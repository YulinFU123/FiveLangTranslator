from __future__ import annotations

import asyncio

from app.core.models import (
    RecognitionResult,
    SourceType,
    SpeechSegment,
    SubtitleStatus,
    SubtitleViewModel,
    new_id,
)


class SubtitlePipelineController:
    """Arbitrates recognition and translation into a single subtitle view model."""

    def __init__(self, bus, arbiter, translation_service) -> None:
        self.bus = bus
        self.arbiter = arbiter
        self.translation_service = translation_service
        translation_service.translated.connect(self.submit_translation)

    def submit_recognition(self, result: RecognitionResult) -> None:
        if not self.arbiter.accept_recognition(result):
            return
        self._emit_view(result.segment_id)
        self.translation_service.submit(result)

    def submit_translation(self, result) -> None:
        if not self.arbiter.accept_translation(result):
            return
        self._emit_view(result.segment_id)

    def _emit_view(self, segment_id: str) -> None:
        state = self.arbiter.get(segment_id)
        if not state or not state.recognition:
            return
        recognition = state.recognition
        translation = state.translation
        metadata = recognition.metadata or {}
        translated_text = translation.translated_text if translation else ""
        target_language = translation.target_language if translation else self.translation_service.target_language
        self.bus.subtitle.emit(SubtitleViewModel(
            recognition.segment_id,
            max(recognition.revision, translation.revision if translation else 0),
            recognition.text,
            translated_text,
            translation.status if translation else recognition.status,
            recognition.source,
            metadata.get("stable_source_text", recognition.text if recognition.status != SubtitleStatus.DRAFT else ""),
            metadata.get("draft_source_text", recognition.text if recognition.status == SubtitleStatus.DRAFT else ""),
            recognition.language,
            target_language,
            getattr(recognition, "start_ms", 0),
            getattr(recognition, "end_ms", 0),
            translation.provider if translation else "",
            getattr(translation, "model", "") if translation else "",
            int(getattr(translation, "latency_ms", 0) or 0) if translation else 0,
            bool(getattr(translation, "cache_hit", False)) if translation else False,
        ))


class DemoController:
    def __init__(self, pipeline, registry) -> None:
        self.pipeline = pipeline
        self.registry = registry
        self.task: asyncio.Task | None = None

    def start(self) -> None:
        if self.task and not self.task.done():
            self.task.cancel()
        self.task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        segment = SpeechSegment(
            new_id(), b"", 16000, 0, 4200,
            SourceType.SYSTEM_AUDIO, "en",
        )
        provider = self.registry.get("mock_asr")
        async for result in provider.recognize_stream(segment):
            self.pipeline.submit_recognition(result)
