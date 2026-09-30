from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:  # pragma: no cover - depends on the installed package
    from winsdk.windows.globalization import Language
    from winsdk.windows.graphics.imaging import BitmapDecoder
    from winsdk.windows.media.ocr import OcrEngine
    from winsdk.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

    OCR_AVAILABLE = True
    OCR_IMPORT_ERROR = ""
except Exception as _exc:  # noqa: BLE001 - optional dependency
    OCR_AVAILABLE = False
    OCR_IMPORT_ERROR = str(_exc)
    logger.warning("Windows OCR 后端不可用：%s", _exc)


class WindowsOcrEngine:
    """Thin async wrapper over ``Windows.Media.Ocr`` (Windows 10/11 built-in).

    Uses the OS engine, so no model download and no extra memory footprint. The
    recognised language must be installed as an OCR language pack in Windows
    settings; ``available_languages`` reports what is actually usable.
    """

    def __init__(self, language_tag: str = "en-US") -> None:
        self.language_tag = language_tag
        self._engine = None

    # -- capability ------------------------------------------------------
    @staticmethod
    def available_languages() -> list[tuple[str, str]]:
        """Returns ``(tag, display_name)`` for every installed OCR language."""
        if not OCR_AVAILABLE:
            return []
        try:
            langs = OcrEngine.available_recognizer_languages
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("枚举 OCR 语言失败：%s", exc)
            return []
        result = []
        for lang in langs:
            try:
                result.append((lang.language_tag, lang.display_name))
            except Exception:  # pragma: no cover - defensive
                continue
        return result

    @staticmethod
    def default_language() -> str | None:
        """Best-effort default: prefer a user-profile language that is installed."""
        langs = WindowsOcrEngine.available_languages()
        return langs[0][0] if langs else None

    # -- engine ----------------------------------------------------------
    def _ensure_engine(self):
        """Creates (or recreates) the native engine for the current language."""
        if not OCR_AVAILABLE:
            return None
        if self._engine is not None:
            return self._engine
        engine = None
        try:
            engine = OcrEngine.try_create_from_language(Language(self.language_tag))
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("创建 OCR 引擎(%s)失败：%s", self.language_tag, exc)
        if engine is None:
            # Fall back to whatever the user profile offers.
            try:
                engine = OcrEngine.try_create_from_user_profile_languages()
                logger.warning(
                    "OCR 语言 %s 不可用，已回退到用户配置文件语言", self.language_tag
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.error("回退 OCR 引擎失败：%s", exc)
        self._engine = engine
        return engine

    def set_language(self, language_tag: str) -> None:
        if language_tag != self.language_tag:
            self.language_tag = language_tag
            self._engine = None  # force recreation on next use

    # -- recognition -----------------------------------------------------
    async def recognize_png(self, png_bytes: bytes) -> str:
        """Recognises text in a PNG image and returns the joined lines."""
        engine = self._ensure_engine()
        if engine is None or not png_bytes:
            return ""
        try:
            stream = InMemoryRandomAccessStream()
            writer = DataWriter(stream)
            writer.write_bytes(png_bytes)
            await writer.store_async()
            await writer.flush_async()
            # Detach so the decoder can read from the start of the stream.
            writer.detach_stream()
            decoder = await BitmapDecoder.create_async(stream)
            bitmap = await decoder.get_software_bitmap_async()
            result = await engine.recognize_async(bitmap)
            return "\n".join(line.text for line in result.lines).strip()
        except Exception as exc:  # noqa: BLE001 - OCR must never kill the loop
            logger.error("OCR 识别失败：%s", exc)
            return ""
