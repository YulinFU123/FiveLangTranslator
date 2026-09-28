"""Unit tests for the translation error classifier (no Qt dependency)."""

from app.translation.errors import TranslationProviderError


def test_connection_refused_maps_winerror_10061():
    err = TranslationProviderError(
        "Ollama 本地翻译",
        "连接失败：<urlopen error [WinError 10061] 由于目标计算机积极拒绝，无法连接。>",
    )
    assert err.category == TranslationProviderError.CATEGORY_CONNECTION_REFUSED
    assert "本地服务未启动" in err.message
    assert "Ollama 本地翻译" in str(err)


def test_connection_refused_maps_bare_message():
    err = TranslationProviderError("Ollama 本地翻译", "连接失败")
    assert err.category == TranslationProviderError.CATEGORY_CONNECTION_REFUSED


def test_auth_failure_maps_401():
    err = TranslationProviderError("DeepSeek", "HTTP 401: Authentication Fails (governor)")
    assert err.category == TranslationProviderError.CATEGORY_AUTH_FAILED
    assert "密钥认证失败" in err.message
    assert "API Key" in err.hint


def test_rate_limited_maps_429():
    err = TranslationProviderError("DeepSeek", "HTTP 429: rate limit exceeded")
    assert err.category == TranslationProviderError.CATEGORY_RATE_LIMITED


def test_other_http_error_includes_status_code():
    err = TranslationProviderError("Ollama 本地翻译", "HTTP 500: internal error")
    assert err.category == TranslationProviderError.CATEGORY_HTTP_ERROR
    assert "500" in err.message


def test_empty_response_maps_malformed_json():
    err = TranslationProviderError("Ollama 本地翻译", "服务返回的不是有效JSON")
    assert err.category == TranslationProviderError.CATEGORY_EMPTY_RESPONSE


def test_unknown_falls_back_to_generic_message():
    err = TranslationProviderError("Ollama 本地翻译", "some unexpected boom")
    assert err.category == TranslationProviderError.CATEGORY_UNKNOWN
    assert "翻译请求失败" in err.message
