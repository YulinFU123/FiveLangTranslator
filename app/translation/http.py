from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


# Human-readable causes for the status codes the translation APIs actually return.
HTTP_ERROR_HINTS = {
    401: "密钥无效或未授权（401）",
    402: "账号余额不足，请到服务商后台充值（402）",
    403: "无权限访问该资源（403）",
    404: "接口地址不存在，请检查 Base URL（404）",
    429: "请求过于频繁，已触发限流（429）",
}


def _describe_http_error(code: int, body: str) -> str:
    """Prefixes the raw API payload with a Chinese explanation of the status."""
    hint = HTTP_ERROR_HINTS.get(code)
    detail = body[-500:].strip()
    return f"{hint} · {detail}" if hint else f"HTTP {code} · {detail}"


def post_json(url: str, payload: dict, timeout: float = 60.0, headers: dict | None = None) -> dict:
    request_headers = {"Content-Type": "application/json"}
    request_headers.update(headers or {})
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(_describe_http_error(exc.code, body)) from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"连接失败：{exc}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("服务返回的不是有效JSON") from exc


def get_json(url: str, timeout: float = 10.0, headers: dict | None = None) -> dict:
    """GET + JSON decode. ``headers`` lets callers send Bearer auth.

    DeepSeek's /models endpoint requires authentication, so a header-less health
    check would report the service as down even with a perfectly valid key.
    """
    request = Request(url, headers=headers or {}, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(_describe_http_error(exc.code, body)) from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"连接失败：{exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("服务返回的不是有效JSON") from exc
