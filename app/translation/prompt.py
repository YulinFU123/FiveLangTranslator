from __future__ import annotations

DEFAULT_MAX_LINES = 2
LINE_BUDGET_MINIMUM = 1
LINE_BUDGET_MAXIMUM = 8

LANGUAGE_NAMES = {
    "auto": "the detected source language",
    "zh": "Simplified Chinese",
    "en": "English",
    "ja": "Japanese",
    "ru": "Russian",
    "de": "German",
}


def clamp_line_budget(value, minimum: int = LINE_BUDGET_MINIMUM, maximum: int = LINE_BUDGET_MAXIMUM) -> int:
    """Clamps a visible line count into the supported budget range."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_LINES
    return max(minimum, min(maximum, number))


def line_budget_clause(max_lines) -> str:
    """Line budget sentence, empty when the box already shows the default two lines."""
    number = int(max_lines or 0)
    if number <= 0 or number == DEFAULT_MAX_LINES:
        return ""
    return (
        f" The subtitle box shows at most {number} lines."
        f" Keep the translation within {number} lines."
    )


def build_messages(job) -> list[dict[str, str]]:
    target = LANGUAGE_NAMES.get(job.target_language, job.target_language)
    source = LANGUAGE_NAMES.get(job.source_language, job.source_language)
    style = {
        # The line count itself is driven by the subtitle box height (max_lines).
        "cinema": "Use concise, natural movie-subtitle wording. Preserve names, numbers, negation, and tone.",
        "literal": "Translate faithfully and literally without adding explanations.",
        "complete": "Translate completely and naturally without omitting information.",
    }.get(job.style, job.style)
    glossary = "\n".join(f"{key} => {value}" for key, value in job.glossary.items())
    context = "\n".join(job.context[-3:])
    line_budget = line_budget_clause(getattr(job, "max_lines", DEFAULT_MAX_LINES))
    system = (
        f"You are a professional subtitle translator. Translate from {source} to {target}. "
        f"{style}{line_budget} Return only the translation, with no commentary, labels, quotation marks, or markdown."
    )
    user_parts = []
    if context:
        user_parts.append("Previous subtitle context:\n" + context)
    if glossary:
        user_parts.append("Required glossary:\n" + glossary)
    user_parts.append("Text to translate:\n" + job.text)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]


def clean_translation(text: str) -> str:
    result = text.strip()
    prefixes = ("Translation:", "译文：", "翻译：", "翻訳：")
    for prefix in prefixes:
        if result.startswith(prefix):
            result = result[len(prefix):].strip()
    if len(result) >= 2 and result[0] == result[-1] and result[0] in {'"', "'", "“", "”"}:
        result = result[1:-1].strip()
    return result
