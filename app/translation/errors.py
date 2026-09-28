from __future__ import annotations

import re


class TranslationProviderError:
    """Categorizes a raw provider exception into a user-facing, actionable error.

    ``TranslationService`` uses this to summarize a failed fallback chain: instead
    of dumping raw technical tracebacks (``连接失败：<urlopen error [WinError 10061]
    ...>``) the UI shows *why* each provider failed, e.g. local service down vs.
    bad key, so the user knows what to fix without opening the log.
    """

    CATEGORY_CONNECTION_REFUSED = "connection_refused"
    CATEGORY_AUTH_FAILED = "auth_failed"
    CATEGORY_RATE_LIMITED = "rate_limited"
    CATEGORY_EMPTY_RESPONSE = "empty_response"
    CATEGORY_HTTP_ERROR = "http_error"
    CATEGORY_UNKNOWN = "unknown"

    def __init__(self, provider_title: str, raw: str) -> None:
        self.provider_title = provider_title
        self.raw = raw or ""
        self.category, self.message, self.hint = self.classify(self.raw)

    @staticmethod
    def classify(raw: str) -> tuple[str, str, str]:
        """Returns ``(category, friendly_message, troubleshooting_hint)``."""
        text = raw or ""
        lowered = text.lower()
        # Local service not reachable: WinError 10061 / URLError "连接失败".
        if "winerror 10061" in lowered or "连接失败" in text or "urlerror" in lowered:
            return (
                TranslationProviderError.CATEGORY_CONNECTION_REFUSED,
                "本地服务未启动或无法连接",
                "请确认该翻译服务已运行（如 Ollama 需启动并拉取对应模型）",
            )
        # Authentication failures: HTTP 401 or provider-specific wording.
        if "401" in text or "authentication" in lowered or "鉴权" in text or "unauthorized" in lowered:
            return (
                TranslationProviderError.CATEGORY_AUTH_FAILED,
                "密钥认证失败",
                "请检查该 Provider 的 API Key 是否正确且有效",
            )
        # Rate limiting.
        if "429" in text or "rate limit" in lowered or "限流" in text:
            return (
                TranslationProviderError.CATEGORY_RATE_LIMITED,
                "请求过于频繁被限流",
                "请稍后重试或降低并发请求数",
            )
        # Other HTTP status errors.
        if "http" in lowered:
            match = re.search(r"(\d{3})", text)
            code = match.group(1) if match else "未知"
            return (
                TranslationProviderError.CATEGORY_HTTP_ERROR,
                f"服务返回错误（HTTP {code}）",
                "请查看该服务的返回内容或日志以获取详情",
            )
        # Empty / malformed upstream responses.
        if "空译文" in text or "未返回" in text or "json" in lowered:
            return (
                TranslationProviderError.CATEGORY_EMPTY_RESPONSE,
                "服务返回内容不可用",
                "请检查模型是否加载正常或返回格式是否符合预期",
            )
        return (
            TranslationProviderError.CATEGORY_UNKNOWN,
            "翻译请求失败",
            "请查看日志获取详细错误信息",
        )

    def __str__(self) -> str:
        return f"{self.provider_title}：{self.message}（{self.hint}）"
