from __future__ import annotations

import hashlib
import re
from typing import Any

# Ordered: the first match wins per substring, and later patterns never see text
# that an earlier pattern already replaced, which keeps replacements stable.
PII_PATTERNS: dict[str, str] = {
    "email": r"[\w\.-]+@[\w\.-]+\.\w+",
    "phone_vn": r"(?<!\d)(?:\+84|0)(?:[ .-]?\d){9}(?!\d)",
    # 12 digits, optionally grouped the way people actually write CCCD
    # ("123 456 789 012"). \b keeps it from eating parts of a 16-digit card.
    "cccd": r"\b\d{12}\b|\b\d{3}[ .-]\d{3}[ .-]\d{3}[ .-]\d{3}\b",
    "credit_card": r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b",
    # Vietnamese passport: one or two leading letters + 7-8 digits (C1234567).
    "passport": r"\b[A-Z]{1,2}\d{7,8}\b",
    # Vietnamese street address, anchored on the address keyword so ordinary
    # prose is not redacted. Accepts both accented and unaccented spellings,
    # because unaccented Vietnamese is extremely common in real input.
    # Stops at the next clause separator.
    "vn_address": (
        r"(?i)\b(?:số|so)\s*\d+[a-z]?[,\s]+"
        r"(?:đường|duong|phố|pho|ngõ|ngo|hẻm|hem|phường|phuong|quận|quan|huyện|huyen)"
        r"\b[^,;\n]{0,60}"
    ),
}


def scrub_text(text: str) -> str:
    safe = text
    for name, pattern in PII_PATTERNS.items():
        safe = re.sub(pattern, f"[REDACTED_{name.upper()}]", safe)
    return safe


def scrub_value(value: Any) -> Any:
    """Recursively scrub every string in a log/trace payload.

    Scrubbing is structural rather than field-by-field: a new log call site
    cannot leak PII just because nobody remembered to wrap it in
    ``summarize_text``. Keys are left untouched so field names stay queryable.
    """
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, dict):
        return {key: scrub_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub_value(item) for item in value]
    return value


def summarize_text(text: str, max_len: int = 80) -> str:
    safe = scrub_text(text).strip().replace("\n", " ")
    return safe[:max_len] + ("..." if len(safe) > max_len else "")


def hash_user_id(user_id: str) -> str:
    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:12]
