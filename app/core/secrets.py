from __future__ import annotations

import base64
import ctypes
import logging
import sys
from ctypes import wintypes

logger = logging.getLogger(__name__)


class _DataBlob(ctypes.Structure):
    """Win32 DATA_BLOB used by CryptProtectData / CryptUnprotectData."""

    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _to_blob(raw: bytes) -> _DataBlob:
    buffer = ctypes.create_string_buffer(raw, len(raw))
    blob = _DataBlob()
    blob.cbData = len(raw)
    blob.pbData = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))
    # Keep a reference alive until the Win32 call returns.
    blob._buffer = buffer  # type: ignore[attr-defined]
    return blob


def _blob_to_bytes(blob: _DataBlob) -> bytes:
    return ctypes.string_at(blob.pbData, blob.cbData)


def is_supported() -> bool:
    """DPAPI is Windows-only; other platforms fall back to no encryption."""
    return sys.platform == "win32"


def protect(secret: str) -> str:
    """Encrypts a secret with the current Windows user's key (DPAPI).

    The result is base64 so it can live inside the JSON settings file. The key is
    bound to the Windows user account, so copying the file to another account or
    machine yields undecryptable data (fail-closed, not silently plaintext).
    """
    if not secret:
        return ""
    if not is_supported():  # pragma: no cover - non-Windows dev hosts
        logger.warning("非 Windows 平台，密钥不做加密存储")
        return ""
    raw = secret.encode("utf-16-le")
    blob_in = _to_blob(raw)
    blob_out = _DataBlob()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    )
    if not ok:
        raise OSError("DPAPI 加密失败，密钥未保存")
    try:
        return base64.b64encode(_blob_to_bytes(blob_out)).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def unprotect(payload: str) -> str:
    """Decrypts a value produced by :func:`protect`. Empty input -> empty output."""
    if not payload:
        return ""
    if not is_supported():  # pragma: no cover - non-Windows dev hosts
        return ""
    try:
        raw = base64.b64decode(payload)
    except Exception:  # noqa: BLE001 - corrupt entry, treat as absent
        logger.warning("密钥密文格式损坏，按未配置处理")
        return ""
    blob_in = _to_blob(raw)
    blob_out = _DataBlob()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    )
    if not ok:
        logger.warning("DPAPI 解密失败（可能更换了 Windows 账户）")
        return ""
    try:
        return _blob_to_bytes(blob_out).decode("utf-16-le")
    except Exception:  # noqa: BLE001
        return ""
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)
