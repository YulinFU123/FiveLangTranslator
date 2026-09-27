# Translation architecture

## Providers

- Ollama: `POST /api/chat`, non-streaming, model keep-alive enabled.
- OpenAI Compatible: `POST /chat/completions`, bearer token read from an environment variable.
- DeepSeek / 豆包（火山方舟）: no new class, they are presets of the OpenAI Compatible driver.

## Provider registry and fallback chain

`app/translation/registry.py` holds every preset and builds the ordered chain:

| Provider id | Driver | Default base | Default model | API key env |
|---|---|---|---|---|
| `ollama` | ollama | `http://127.0.0.1:11434` | `qwen3:4b` | – |
| `openai_compatible` | openai_compatible | `http://127.0.0.1:1234/v1` | `local-model` | `OPENAI_API_KEY` |
| `deepseek` | openai_compatible | `https://api.deepseek.com` | `deepseek-chat` | `DEEPSEEK_API_KEY` |
| `doubao` | openai_compatible | `https://ark.cn-beijing.volces.com/api/v3` | (endpoint id, required) | `ARK_API_KEY` |

- `translation_chain` in the settings file is the fallback order; `chain[0]` answers first.
- `translation_endpoints` overrides `base_url` / `model` per provider; the legacy `ollama_url`,
  `ollama_model`, `openai_compatible_*` fields still work.
- Cache lookups walk the chain in order, so each provider keeps its own namespace
  (a fallback answer is never stored under the primary provider's key).
- On failure the service moves to the next provider and only reports an error when the whole chain
  failed; `metrics_changed` carries `chain_position` and `fallback_title`.

Using DeepSeek today (before the UI gets the new dropdowns) means editing
`%USERPROFILE%\.five_lang_translator\settings.json`:

```json
{
  "translation_chain": ["deepseek", "ollama"],
  "translation_endpoints": {"deepseek": {"model": "deepseek-chat"}}
}
```

and starting with `$env:DEEPSEEK_API_KEY="..."` in the session.

## Scheduling

```text
DRAFT -> debounce -> cancel old draft -> translate latest
STABLE/FINAL -> cancel draft -> translate immediately
```

A returned translation is discarded when its revision is older than the latest recognition revision for the segment.

## Context

Only FINAL pairs are committed to the rolling context. Drafts never pollute future prompts.

## Line budget

`max_lines` is the number of visible lines the subtitle box can show. It is derived from the box
height and the current font size, carried by `TranslationJob.max_lines` (default 2, clamped to 1–8)
and injected into the system prompt as a line budget sentence:

```text
The subtitle box shows at most N lines. Keep the translation within N lines.
```

The sentence is omitted when N is 2 (or missing), so the default prompt stays short. The cinema
style no longer hardcodes "two short lines": line count comes from N only, while style still
controls wording, tone and omission behaviour.

### Where N comes from

`OverlayWindow.visible_lines()` measures it: translation label `QFontMetrics.lineSpacing()` divided
into the panel content height (box height − margins − spacing − border − the source line when it is
visible). The result is clamped to 1–8.

Recomputation triggers: first `showEvent`, `resizeEvent`, `moveEvent`, `ScreenChangeInternal`,
font database reload, and every `refresh()` (font size / family / weight changes). Each request is
debounced 100 ms through a single-shot `QTimer` that is restarted on every new request, so dragging
an edge publishes only the final value. `publish_line_budget()` additionally compares against the
previously published number and stays silent when it did not change, therefore no model call is
triggered when the drag settles on the same N.

The service default is 2, but the first measurement after `show()` is pushed immediately, so the
first subtitles already use the real box height.

Because N changes the translation, it is part of the cache key: `N=2` and `N=4` never share an
entry. Nothing is clipped or re-wrapped at display time — the label renders whatever the model
returns.

## Cache

The cache key contains normalized text, language pair, style, glossary, provider, and model. This prevents stale results from leaking across settings.

Since v0.4.0-alpha.2 the cache is two layered:

```text
lookup -> in-memory LRU -> SQLite (translation_cache) -> provider
write  -> in-memory LRU + SQLite
```

- Memory layer answers repeat translations inside one session with zero model calls.
- SQLite layer keeps translations across restarts; hits are reported as `SQLite命中` in the UI.
- `SqliteCacheStore.prune()` evicts least recently used rows once the 20000 entry limit is exceeded.
- Changing the glossary changes the cache key by design, so old wording is never reused.

## Glossary source

The glossary shown in the UI lives in SQLite (`glossary` table). It can be imported from / exported to JSON and is loaded into `TranslationService` on startup.
