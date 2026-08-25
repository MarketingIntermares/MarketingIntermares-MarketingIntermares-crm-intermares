from __future__ import annotations

import re
import unicodedata
from typing import Any


def normalize_email(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def normalize_phone(value: Any) -> str:
    if value is None:
        return ""
    digits = re.sub(r"\D+", "", str(value))
    if digits.startswith("55") and len(digits) >= 12:
        digits = digits[2:]
    if len(digits) > 11:
        digits = digits[-11:]
    return digits


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower()
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def is_truthy_text(value: Any) -> bool:
    text = normalize_text(value)
    return text not in {"", "nan", "none", "null", "nao", "não", "0", "false"}
