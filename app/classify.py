"""Document classification with LLM support."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .llm_client import LLMClient

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


def classify_text(
    text: str,
    default_category: str = "інше",
    llm_client: Optional["LLMClient"] = None,
) -> Dict[str, Optional[str]]:
    """
    Класифікувати текст документа.

    Якщо надано llm_client і він увімкнений, використовується LLM.
    Інакше використовуються прості евристики на основі ключових слів.
    """
    # Спробувати LLM якщо доступний
    if llm_client and llm_client.enabled and text.strip():
        llm_category, llm_date, llm_summary = llm_client.analyze_document(text)
        if llm_category or llm_date:
            return {
                "category": llm_category or default_category,
                "date_doc": llm_date,
                "summary": llm_summary,
            }

    # Fallback: прості евристики
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

    return {"category": category, "date_doc": date_doc, "summary": None}


def summarize_text(
    text: str,
    limit: int = 200,
    llm_client: Optional["LLMClient"] = None,
) -> str:
    """
    Створити короткий опис тексту.

    Якщо надано llm_client і він увімкнений, використовується LLM для анотації.
    Інакше просто обрізається початок тексту.
    """
    # LLM вже викликався в classify_text, тут просто fallback
    cleaned = " ".join(text.split())
    return cleaned[:limit]

