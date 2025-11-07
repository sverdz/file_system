"""File renaming utilities."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List

from unidecode import unidecode

from .scan import FileMeta
from .config import MAX_FILENAME_LENGTH

INVALID_CHARS = re.compile(r"[^A-Za-z0-9_.-]+")
SAFE_CHARS_ONLY = re.compile(r"[^A-Za-z0-9_-]+")


def slugify(text: str, limit: int = 50) -> str:
    """Legacy slugify function for backward compatibility."""
    transliterated = unidecode(text).strip().lower()
    cleaned = INVALID_CHARS.sub("-", transliterated)
    cleaned = re.sub(r"-+", "-", cleaned)
    return cleaned[:limit].strip("-") or "document"


def sanitize_filename_component(text: str) -> str:
    """
    Очищення компоненту імені файлу - тільки безпечні символи [A-Za-z0-9_-].

    Args:
        text: Вхідний текст (може бути українською)

    Returns:
        Очищений текст тільки з безпечними символами
    """
    # Транслітерація кирилиці в латиницю
    transliterated = unidecode(text).strip()
    # Видалити всі небезпечні символи
    cleaned = SAFE_CHARS_ONLY.sub("", transliterated)
    # Видалити повторювані дефіси/підкреслення
    cleaned = re.sub(r"[-_]+", "_", cleaned)
    return cleaned.strip("_-") or "doc"


def generate_short_suffix(index: int) -> str:
    """
    Генерація короткого суфікса для унікальності.

    Спочатку літери: a, b, c, ... z (26 варіантів)
    Потім цифри: 1, 2, 3, ... 99 (99 варіантів)

    Args:
        index: Індекс від 0

    Returns:
        Короткий суфікс (1-2 символи)
    """
    if index < 26:
        # a-z
        return chr(ord('a') + index)
    else:
        # 1, 2, 3, ... 99
        return str(index - 25)


def build_short_filename(
    date_str: str,
    category: str,
    suffix_index: int = 0,
    extension: str = "",
    use_short_date: bool = False,
    max_length: int = MAX_FILENAME_LENGTH
) -> str:
    """
    Створення короткого імені файлу з жорстким обмеженням довжини.

    Структура: {DATE}_{CATEGORY}_{SUFFIX}{EXT}
    - DATE: YYYYMMDD (8) або YYMMDD (6) символів
    - CATEGORY: скорочена класифікація (автоматично усічена)
    - SUFFIX: a-z або 1-99 (1-2 символи)
    - EXT: розширення файлу (не враховується в max_length)

    Args:
        date_str: Дата у форматі YYYYMMDD або YYMMDD
        category: Класифікація документа
        suffix_index: Індекс для генерації суфікса (0 = 'a', 1 = 'b', ...)
        extension: Розширення файлу (з крапкою)
        use_short_date: Використовувати YYMMDD замість YYYYMMDD
        max_length: Максимальна довжина без розширення (за замовчуванням 20)

    Returns:
        Ім'я файлу з обмеженням max_length символів (без розширення)

    Examples:
        >>> build_short_filename("20241107", "договір", 0, ".pdf")
        '20241107_dohovir_a.pdf'
        >>> build_short_filename("241107", "рахунок-фактура", 1, ".pdf", use_short_date=True)
        '241107_rakhuno_b.pdf'
    """
    # Очищення та підготовка компонентів
    date_clean = date_str.strip()
    category_clean = sanitize_filename_component(category)
    suffix = generate_short_suffix(suffix_index)

    # Довжини компонентів
    date_len = len(date_clean)
    suffix_len = len(suffix)
    separators_len = 2  # Два підкреслення: _{category}_ і _{suffix}

    # Обчислення максимальної довжини для категорії
    max_category_len = max_length - date_len - suffix_len - separators_len

    # Усічення категорії якщо потрібно
    if max_category_len < 1:
        # Якщо навіть немає місця для категорії - використати мінімум
        category_clean = "d"
        # Перерахувати з мінімальною категорією
        max_category_len = 1
    elif len(category_clean) > max_category_len:
        category_clean = category_clean[:max_category_len]

    # Формування імені
    name_without_ext = f"{date_clean}_{category_clean}_{suffix}"

    # Остаточна перевірка довжини (на всяк випадок)
    if len(name_without_ext) > max_length:
        # Критичне усічення - обрізати категорію ще більше
        overflow = len(name_without_ext) - max_length
        category_clean = category_clean[:max(1, len(category_clean) - overflow)]
        name_without_ext = f"{date_clean}_{category_clean}_{suffix}"

    # Додати розширення
    full_name = name_without_ext + extension

    return full_name


@dataclass
class RenamePlan:
    meta: FileMeta
    new_name: str
    collision: bool = False


def build_filename(template: str, context: Dict[str, str]) -> str:
    name = template.format(**context)
    name = re.sub(r"[\\/:*?\"<>|]", "_", name)
    return name


def plan_renames(
    files: List[FileMeta],
    template: str,
    contexts: Dict[Path, Dict[str, str]],
    use_short_format: bool = True,
    use_short_date: bool = False
) -> List[RenamePlan]:
    """
    Планування перейменування файлів з жорстким обмеженням довжини.

    Args:
        files: Список файлів для перейменування
        template: Шаблон назви (використовується лише якщо use_short_format=False)
        contexts: Контекст для кожного файлу (category, date_doc, тощо)
        use_short_format: Використовувати короткий формат з обмеженням 20 символів
        use_short_date: Використовувати короткий формат дати YYMMDD замість YYYYMMDD

    Returns:
        Список RenamePlan з новими іменами та позначками колізій
    """
    plans: List[RenamePlan] = []
    used: Dict[Path, set[str]] = {}

    for meta in sorted(files, key=lambda m: str(m.path)):
        ctx = contexts.get(meta.path, {}).copy()
        parent = meta.path.parent
        used.setdefault(parent, set())

        if use_short_format:
            # Новий короткий формат з обмеженням 20 символів
            # Отримання дати та категорії
            category = ctx.get("category", "інше")
            date_doc = ctx.get("date_doc", "")

            # Форматування дати
            if date_doc and len(date_doc) >= 10:  # ISO формат YYYY-MM-DD
                date_parts = date_doc.split("-")
                if len(date_parts) == 3:
                    yyyy, mm, dd = date_parts[0], date_parts[1], date_parts[2]
                    if use_short_date:
                        date_str = f"{yyyy[2:4]}{mm.zfill(2)}{dd.zfill(2)}"  # YYMMDD
                    else:
                        date_str = f"{yyyy}{mm.zfill(2)}{dd.zfill(2)}"  # YYYYMMDD
                else:
                    date_str = "20241107" if not use_short_date else "241107"
            else:
                # Якщо дата відсутня - використати поточну або дату файлу
                from datetime import datetime
                file_date = datetime.fromtimestamp(meta.mtime)
                if use_short_date:
                    date_str = file_date.strftime("%y%m%d")
                else:
                    date_str = file_date.strftime("%Y%m%d")

            extension = meta.path.suffix

            # Генерація імені з унікальним суфіксом
            suffix_index = 0
            collision = False
            while True:
                candidate = build_short_filename(
                    date_str=date_str,
                    category=category,
                    suffix_index=suffix_index,
                    extension=extension,
                    use_short_date=use_short_date
                )

                if candidate not in used[parent]:
                    break

                suffix_index += 1
                collision = True

                # Безпека: максимум 1000 спроб
                if suffix_index > 1000:
                    candidate = build_short_filename(
                        date_str=date_str,
                        category=category,
                        suffix_index=suffix_index,
                        extension=extension,
                        use_short_date=use_short_date
                    )
                    break

            used[parent].add(candidate)
            plans.append(RenamePlan(meta=meta, new_name=candidate, collision=collision))

        else:
            # Старий формат через шаблон (для зворотної сумісності)
            ctx.setdefault("short_title", slugify(ctx.get("short_title", meta.path.stem)))
            ctx.setdefault("hash8", (meta.sha256 or "0" * 8)[:8])
            ctx.setdefault("ext", meta.path.suffix)

            if "version" in ctx:
                if isinstance(ctx["version"], str):
                    try:
                        ctx["version"] = int(ctx["version"])
                    except ValueError:
                        ctx["version"] = 1
            else:
                ctx["version"] = 1

            name = build_filename(template, ctx)
            version = ctx["version"]
            candidate = name
            collision = False
            while candidate in used[parent]:
                version += 1
                ctx["version"] = version
                candidate = build_filename(template, ctx)
                collision = True

            used[parent].add(candidate)
            plans.append(RenamePlan(meta=meta, new_name=candidate, collision=collision))

    return plans

