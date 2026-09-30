from __future__ import annotations

import asyncio
from time import monotonic

from PySide6.QtCore import QObject, Signal

from app.core.models import RecognitionResult, SubtitleStatus, TranslationResult
from app.translation.cache import TranslationCache
from app.translation.context import DEFAULT_CONTEXT_SENTENCES, TranslationContext
from app.translation.models import TranslationJob
from app.translation.prompt import DEFAULT_MAX_LINES, clamp_line_budget
from app.translation.errors import TranslationProviderError
from app.translation.stats import LatencyTracker


class TranslationService(QObject):
    translated = Signal(object)
    status_changed = Signal(str)
    metrics_changed = Signal(object)
    error = Signal(str)

    def __init__(self, cache=None, context_sentences: int = DEFAULT_CONTEXT_SENTENCES) -> None:
        super().__init__()
        # Ordered fallback chain: chain[0] is the primary provider.
        self.chain: list = []
        self.providers: dict[str, object] = {}
        self.primary_id = "ollama"
        self.target_language = "zh"
        self.style = "cinema"
        # DRAFT (interim) translation debounce. Lower = earlier first-visible
        # feedback at the cost of more partial requests. 160ms keeps local-model
        # round-trips cheap while shaving ~120ms off perceived translation lag.
        self.draft_debounce_ms = 160
        self.cache = cache or TranslationCache()
        # How many previous pairs ride along in the prompt. Fewer tokens = faster
        # local inference, trading away a little cross-sentence coherence.
        self.context_sentences = max(0, int(context_sentences))
        self.context = TranslationContext(self.context_sentences)
        self.glossary: dict[str, str] = {}
        self.max_lines = DEFAULT_MAX_LINES
        self.stats = LatencyTracker(50)
        self.draft_tasks: dict[str, asyncio.Task] = {}
        self.final_tasks: set[asyncio.Task] = set()
        # In-flight dedupe: maps a primary-provider cache key to the pending
        # translation future so concurrent identical requests share one call.
        self._inflight: dict[str, asyncio.Future] = {}
        self.inflight_dedup_enabled: bool = True
        self.latest_revision: dict[str, int] = {}
        self.enabled = False

    def set_context_sentences(self, count: int) -> None:
        """Rebuilds the prompt context window with a different sentence budget."""
        count = max(0, int(count))
        if count == self.context_sentences:
            return
        self.context_sentences = count
        self.context = TranslationContext(count)

    def set_providers(self, providers) -> None:
        """Installs the ordered fallback chain built by TranslationProviderRegistry."""
        self.chain = [provider for provider in providers if provider is not None]
        self.providers = {}
        for provider in self.chain:
            key = getattr(provider, "preset_id", "") or provider.provider_id
            self.providers[key] = provider
        self.enabled = bool(self.chain)
        self.primary_id = (
            getattr(self.chain[0], "preset_id", "") or self.chain[0].provider_id
        ) if self.chain else ""
        if not self.enabled:
            self.status_changed.emit("未配置任何翻译Provider")
            return
        names = " → ".join(self._title(provider) for provider in self.chain)
        self.status_changed.emit(f"翻译链已配置 · {names} · {self.target_language} · {self.style}")

    def set_language_pair(self, target_language: str, style: str) -> None:
        self.target_language = target_language
        self.style = style
        self.status_changed.emit(
            f"翻译目标已更新 · {target_language} · {style}"
            + (f" · 当前链 {len(self.chain)} 个 Provider" if self.chain else "")
        )

    def _title(self, provider) -> str:
        return getattr(provider, "display_title", "") or provider.provider_id

    def submit(self, recognition: RecognitionResult) -> None:
        if not self.enabled or not recognition.text.strip():
            return
        if recognition.language == self.target_language:
            result = TranslationResult(
                recognition.segment_id, recognition.revision,
                recognition.text, recognition.text,
                recognition.language, self.target_language,
                recognition.status, "identity", 0,
            )
            self.translated.emit(result)
            return
        self.latest_revision[recognition.segment_id] = max(
            recognition.revision,
            self.latest_revision.get(recognition.segment_id, 0),
        )
        metadata = recognition.metadata or {}
        job = TranslationJob(
            recognition.segment_id,
            recognition.revision,
            recognition.text,
            recognition.language,
            self.target_language,
            recognition.status,
            self.context.snapshot(),
            dict(self.glossary),
            self.style,
            self.max_lines,
        )
        if recognition.status == SubtitleStatus.DRAFT:
            old = self.draft_tasks.get(recognition.segment_id)
            if old and not old.done():
                old.cancel()
            task = asyncio.create_task(self._run_draft(job, metadata))
            self.draft_tasks[recognition.segment_id] = task
        else:
            old = self.draft_tasks.pop(recognition.segment_id, None)
            if old and not old.done():
                old.cancel()
            task = asyncio.create_task(self._run(job, metadata, is_final=True))
            self.final_tasks.add(task)
            task.add_done_callback(self.final_tasks.discard)

    async def _run_draft(self, job, metadata) -> None:
        try:
            await asyncio.sleep(self.draft_debounce_ms / 1000)
            await self._run(job, metadata, is_final=False)
        except asyncio.CancelledError:
            return

    async def _run(self, job, recognition_metadata, is_final: bool) -> None:
        if not self.chain:
            self.error.emit("未配置翻译Provider")
            return
        started = monotonic()
        translated_text = None
        provider_id = "cache"
        used_model = ""
        latency_ms = 0
        cache_source = None
        provider_index = 0
        cache_key = ""
        fallback_used = False

        # Every provider keeps its own cache namespace, so check them in chain
        # order before spending a request.
        for index, provider in enumerate(self.chain):
            model = getattr(provider.config, "model", "")
            key = self.cache.key(job, provider.provider_id, model)
            value, source = self._lookup_cache(key)
            if value is not None:
                translated_text, cache_source = value, source
                provider_index, cache_key, used_model = index, key, model
                break

        # In-flight dedupe: concurrent identical translations (same primary
        # provider key) collapse into one provider call -- important under the
        # load/concurrency scenario. Unique texts (the benchmark default) never
        # collide, so this never distorts latency measurements. Disabled by the
        # benchmark control group (inflight_dedup_enabled=False) to measure the
        # raw, non-deduped upstream latency.
        use_dedup = self.inflight_dedup_enabled
        dedup_key = self.cache.key(
            job, self.chain[0].provider_id,
            getattr(self.chain[0].config, "model", ""),
        )
        if translated_text is None:
            if use_dedup:
                pending = self._inflight.get(dedup_key)
                if pending is not None and not pending.done():
                    try:
                        reused = await pending
                    except Exception:
                        reused = None
                    if reused is not None:
                        (translated_text, provider_id, latency_ms,
                         used_model, cache_key, provider_index) = reused
                        cache_source = None
                        fallback_used = False
            if translated_text is None:
                future = None
                if use_dedup:
                    loop = asyncio.get_running_loop()
                    future = loop.create_future()
                    self._inflight[dedup_key] = future
                failures: list[str] = []
                try:
                    for index, provider in enumerate(self.chain):
                        model = getattr(provider.config, "model", "")
                        key = self.cache.key(job, provider.provider_id, model)
                        try:
                            provider_result = await provider.translate(job)
                        except asyncio.CancelledError:
                            raise
                        except Exception as error:
                            failures.append(TranslationProviderError(self._title(provider), str(error)))
                            continue
                        translated_text = provider_result.text
                        provider_id = provider_result.provider
                        latency_ms = provider_result.latency_ms
                        used_model = provider_result.model or model
                        provider_index, cache_key = index, key
                        fallback_used = index > 0
                        self.cache.put(key, translated_text, job.text, provider_id, used_model)
                        if future is not None:
                            future.set_result(
                                (translated_text, provider_id, latency_ms,
                                 used_model, cache_key, index)
                            )
                        break
                    else:
                        if future is not None:
                            future.set_exception(RuntimeError("翻译失败"))
                            # A lone request (no concurrent caller) never awaits
                            # this dedup future, which would otherwise log a noisy
                            # "Future exception was never retrieved" warning.
                            future.add_done_callback(lambda f: f.exception())
                        self.error.emit("翻译失败 · " + " → ".join(str(f) for f in failures))
                        return
                finally:
                    if future is not None:
                        self._inflight.pop(dedup_key, None)

        if job.revision < self.latest_revision.get(job.segment_id, 0):
            return
        result = TranslationResult(
            job.segment_id,
            job.revision,
            job.text,
            translated_text,
            job.source_language,
            job.target_language,
            job.status,
            provider_id,
            latency_ms,
            used_model,
            cache_source is not None,
        )
        if is_final:
            self.context.commit(job.text, translated_text)
        self.translated.emit(result)
        self.stats.record(latency_ms, cache_source is not None, provider_id, used_model)
        self.metrics_changed.emit({
            **self.stats.snapshot(),
            "provider": provider_id,
            "latency_ms": latency_ms,
            "cached": cache_source is not None,
            "chain_position": provider_index,
            "fallback_title": self._title(self.chain[provider_index]) if self.chain else "--",
            "cache_source": cache_source or "--",
            "fallback": fallback_used,
            "status": job.status.name.lower(),
            "total_elapsed_ms": int((monotonic() - started) * 1000),
        })

    def _lookup_cache(self, key: str) -> tuple[str | None, str | None]:
        """Returns the cached translation and where it came from."""
        if hasattr(self.cache, "lookup"):
            return self.cache.lookup(key)
        value = self.cache.get(key)
        return value, ("memory" if value is not None else None)

    def set_max_lines(self, value) -> bool:
        """Updates the visible line budget. Returns True when it actually changed."""
        clamped = clamp_line_budget(value)
        if clamped == self.max_lines:
            return False
        self.max_lines = clamped
        return True

    def set_glossary(self, glossary: dict[str, str]) -> None:
        self.glossary = dict(glossary or {})
        self.status_changed.emit(
            f"术语表已更新 · {len(self.glossary)} 条 · 缓存键随术语表变化"
        )

    def set_draft_debounce(self, ms: float) -> None:
        """Tunes the interim-translation debounce (see ``draft_debounce_ms``)."""
        self.draft_debounce_ms = max(0.0, float(ms))

    def set_inflight_dedup(self, enabled: bool) -> None:
        """Enables/disables concurrent same-sentence in-flight deduplication.

        Disabling it is used by the benchmark control group so the raw,
        non-deduped upstream latency is measured without interference.
        """
        self.inflight_dedup_enabled = bool(enabled)

    async def health_check(self, provider_id: str | None = None) -> dict:
        """Checks every provider in the chain, or just the selected one."""
        targets = self.chain
        if provider_id:
            targets = [provider for provider in self.chain if self._identity(provider) == provider_id] or self.chain
        status = {}
        for provider in targets:
            try:
                ok = await provider.health_check()
            except Exception as exc:
                ok = f"不可用：{exc}"
            status[self._identity(provider)] = ok
        return status

    def _identity(self, provider) -> str:
        return getattr(provider, "preset_id", "") or provider.provider_id

    def cancel_all(self) -> None:
        for task in self.draft_tasks.values():
            task.cancel()
        for task in self.final_tasks:
            task.cancel()
        self.draft_tasks.clear()
        self.final_tasks.clear()
