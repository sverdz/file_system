"""Simple document classification heuristics."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Dict, Optional
import requests

CATEGORY_KEYWORDS = {
    "договір": ["договір", "contract"],
    "рахунок": ["рахунок", "invoice"],
    "акт": ["акт виконаних", "акт"],
    "протокол": ["протокол"],
    "лист": ["лист", "letter"],
    "наказ": ["наказ", "order"],
    "звіт": ["звіт", "report"],
    "кошторис": ["кошторис", "estimate"],
    "тендер": ["тендер", "bid"],
    "презентація": ["презентація", "presentation"],
    "довідка": ["довідка", "certificate"],
    "ТЗ": ["технічне завдання", "ТЗ"],
    "специфікація": ["специфікація", "specification"],
}

DATE_PATTERN = re.compile(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})")


def classify_text(text: str, default_category: str = "інше") -> Dict[str, Optional[str]]:
    category = default_category
    lowered = text.lower()
    for name, keywords in CATEGORY_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            category = name
            break
    date_doc = None
    match = DATE_PATTERN.search(text)
    if match:
        try:
            year, month, day = map(int, match.groups())
            date_doc = datetime(year, month, day).date().isoformat()
        except ValueError:
            date_doc = None
    return {"category": category, "date_doc": date_doc}


def summarize_text(text: str, limit: int = 200) -> str:
    cleaned = " ".join(text.split())
    return cleaned[:limit]

