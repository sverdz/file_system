# Посібник з рефакторингу інтерфейсу

## Зміни в архітектурі (2024-11-07)

---

## 🔧 1. Виправлення помилки Excel "cannot be used in worksheets"

### Проблема

```python
# ❌ СТАРИЙ КОД - призводив до помилок
with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
    df.to_excel(writer, sheet_name="some:invalid/name*", index=False)
    # ERROR: cannot be used in worksheets
```

### Рішення

```python
# ✅ НОВИЙ КОД - з нормалізацією назв
from app.inventory import normalize_sheet_name

used_names: Set[str] = set()

with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
    # Автоматична нормалізація
    sheet_name = normalize_sheet_name("some:invalid/name*", used_names)
    # Результат: "some_invalid_name"
    df.to_excel(writer, sheet_name=sheet_name, index=False)
```

### Функція normalize_sheet_name()

**Що робить:**
- Видаляє заборонені символи: `: \ / ? * [ ]`
- Обрізає до 31 символа (ліміт Excel)
- Видаляє керуючі та невидимі символи
- Обробляє конфлікти назв (_2, _3, тощо)
- Гарантує fallback якщо порожня

**Приклади:**

```python
from app.inventory import normalize_sheet_name

# Базове використання
name1 = normalize_sheet_name("файл:назва/тест*")
# Результат: "failnazvatest"

# З fallback
name2 = normalize_sheet_name("", fallback="MySheet")
# Результат: "MySheet"

# З трекінгом конфліктів
used = set()
name3 = normalize_sheet_name("inventory", used)  # "inventory"
name4 = normalize_sheet_name("inventory", used)  # "inventory_2"
name5 = normalize_sheet_name("inventory", used)  # "inventory_3"
```

---

## 🎨 2. Централізована конфігурація кольорів

### Нова структура

```python
from app.theme import THEME, markup, format_number, format_status

# Доступ до кольорів
print(THEME.primary_text)    # "white"
print(THEME.success)          # "bright_green"
print(THEME.error)            # "bright_red"
```

### Повна палітра

```python
# Текст та інформація
THEME.primary_text      # "white" - основний текст
THEME.secondary_text    # "bright_white" - виділений
THEME.dim_text          # "grey70" - другорядна інформація

# Статуси
THEME.success           # "bright_green" - успіх
THEME.error             # "bright_red" - помилки
THEME.warning           # "bright_yellow" - попередження
THEME.info              # "bright_cyan" - інформація
THEME.processing        # "bright_blue" - в процесі

# Числові показники
THEME.number_primary    # "bright_cyan"
THEME.number_total      # "bright_white"
THEME.number_success    # "bright_green"
THEME.number_error      # "bright_red"

# Прогрес-бар
THEME.progress_bar      # "bright_cyan"
THEME.progress_text     # "bright_white"
THEME.progress_percent  # "bright_yellow"

# Заголовки
THEME.header            # "bright_cyan"
THEME.border            # "bright_blue"
THEME.title             # "bold bright_white"

# Категорії та файли
THEME.category          # "bright_magenta"
THEME.file_name         # "bright_white"
THEME.file_path         # "grey70"

# Дублікати
THEME.duplicate         # "bright_yellow"
THEME.duplicate_count   # "bright_red"

# LLM
THEME.llm_request       # "bright_magenta"
THEME.llm_response      # "bright_cyan"
THEME.classification    # "bright_green"
```

### Допоміжні функції

```python
from app.theme import (
    markup,
    bold,
    format_number,
    format_percent,
    format_file_name,
    format_category,
    format_status,
    format_error,
    format_info,
    header_line,
    section_line,
)

# Форматування тексту
text = markup(THEME.success, "Успішно!")
# Результат: "[bright_green]Успішно![/bright_green]"

bold_text = bold("Важливо")
# Результат: "[bold]Важливо[/bold]"

# Форматування чисел
num = format_number(1234567)
# Результат: "[bright_cyan]1,234,567[/bright_cyan]"

percent = format_percent(75.5)
# Результат: "[bright_yellow]75.5%[/bright_yellow]"

# Форматування статусів
status = format_status("Готово", is_error=False)
# Результат: "[bright_green]✓ Готово[/bright_green]"

error = format_error("Файл не знайдено")
# Результат: "[bright_red]⚠ Файл не знайдено[/bright_red]"

# Заголовки
header = header_line("МІЙ ЗАГОЛОВОК", width=60)
section = section_line("Секція 1")
```

---

## 📊 3. Компактне відображення прогресу

### Нова архітектура

```python
from app.progress import ProgressTracker

# Створення трекера з компактним виглядом
tracker = ProgressTracker({
    "scan": 1.0,
    "extract": 2.0,
    "classify": 1.0,
})

# Увімкнути компактний режим (за замовчуванням True)
tracker.use_compact_view = True

# Запуск
tracker.start_visual()
```

### Оновлення метрик

```python
# Агреговані метрики
tracker.update_metrics(
    duplicate_groups=5,
    duplicate_files=12,
    error_count=2,
    success_count=245,
    llm_requests=230,
    llm_responses=228,
)
```

### Статус поточного файлу

```python
# Встановити поточний файл
tracker.set_current_file(
    name="document.pdf",
    path="/шлях/до/document.pdf",
    category="договір",
    stage="витяг тексту",
    status="processing",  # "processing", "success", "error"
    error_msg="",  # Якщо є помилка
)

# Відобразити статус
tracker.show_status()
```

### Приклад повного використання

```python
from app.progress import ProgressTracker
from app.theme import THEME, markup

# Ініціалізація
tracker = ProgressTracker({
    "scan": 1.0,
    "extract": 2.0,
    "classify": 1.0,
})

tracker.start_visual()
tracker.set_all_totals(100)

# Цикл обробки файлів
for idx, file in enumerate(files):
    # Встановити поточний файл
    tracker.set_current_file(
        name=file.name,
        category="договір",
        stage="витяг тексту",
        status="processing",
    )

    try:
        # Обробка файлу
        process_file(file)

        # Успіх
        tracker.set_current_file(
            name=file.name,
            status="success",
        )
        tracker.update_metrics(success_count=idx + 1)

    except Exception as e:
        # Помилка
        tracker.set_current_file(
            name=file.name,
            status="error",
            error_msg=str(e),
        )
        tracker.update_metrics(error_count=1)

    # Оновити прогрес
    tracker.increment("extract")

    # Показати статус кожні 10 файлів
    if idx % 10 == 0:
        tracker.show_status()

# Зупинити
tracker.stop_visual()
```

### Вивід компактного статусу

```
┌─ Метрики ──────────────────────────────┐
│ Груп дублікатів:    5                  │
│ Помилок:            2                  │
│ Успішно:            245                │
└────────────────────────────────────────┘

┌─ Поточний файл ────────────────────────┐
│ ⏳ document.pdf                         │
│   Етап: витяг тексту | Категорія: договір │
└────────────────────────────────────────┘
```

---

## 📝 4. Міграція існуючого коду

### Заміна кольорів

```python
# ❌ СТАРИЙ КОД
console.print("[yellow]Попередження[/yellow]")
console.print(f"[blue]Оброблено: {count}[/blue]")

# ✅ НОВИЙ КОД
from app.theme import THEME, markup, format_number

console.print(markup(THEME.warning, "Попередження"))
console.print(f"Оброблено: {format_number(count)}")
```

### Заміна прогрес-бару

```python
# ❌ СТАРИЙ КОД - багато смуг
tracker.update_description("extract", f"Обробка {file.name}")
tracker.update_description("classify", f"Класифікація {file.name}")
# ... десятки рядків

# ✅ НОВИЙ КОД - компактний
tracker.set_current_file(
    name=file.name,
    stage="витяг тексту",
    status="processing",
)
tracker.show_status()  # Один раз показати статус
```

### Заміна створення Excel

```python
# ❌ СТАРИЙ КОД
with pd.ExcelWriter(path, engine="openpyxl") as writer:
    for name, df in sheets.items():
        df.to_excel(writer, sheet_name=name, index=False)

# ✅ НОВИЙ КОД
from app.inventory import normalize_sheet_name

used_names: Set[str] = set()

with pd.ExcelWriter(path, engine="openpyxl") as writer:
    for name, df in sheets.items():
        safe_name = normalize_sheet_name(name, used_names)
        df.to_excel(writer, sheet_name=safe_name, index=False)
```

---

## 🧪 5. Тестування

### Тест нормалізації назв

```python
from app.inventory import normalize_sheet_name

def test_normalize_sheet_name():
    assert normalize_sheet_name("test") == "test"
    assert normalize_sheet_name("test:name") == "test_name"
    assert normalize_sheet_name("a" * 50) == "a" * 31
    assert normalize_sheet_name("") == "Sheet"

    used = set()
    assert normalize_sheet_name("test", used) == "test"
    assert normalize_sheet_name("test", used) == "test_2"
```

### Тест кольорів

```python
from app.theme import THEME, markup

def test_theme():
    assert THEME.success == "bright_green"
    assert markup(THEME.error, "test") == "[bright_red]test[/bright_red]"
```

### Тест прогресу

```python
from app.progress import ProgressTracker

def test_progress():
    tracker = ProgressTracker({"stage1": 1.0})
    tracker.use_compact_view = True

    tracker.update_metrics(success_count=10)
    assert tracker.metrics.success_count == 10

    tracker.set_current_file(name="test.pdf", status="success")
    assert tracker.current_file.name == "test.pdf"
```

---

## 📋 6. Контрольний список міграції

- [ ] Замінити всі прямі виклики `pd.to_excel()` на використання `normalize_sheet_name()`
- [ ] Замінити всі хардкоджені кольори на `THEME.*`
- [ ] Оновити прогрес-бар на компактний вигляд
- [ ] Використовувати `tracker.set_current_file()` замість багатьох `update_description()`
- [ ] Додати виклики `tracker.update_metrics()` для агрегованих показників
- [ ] Тестувати на темному фоні для перевірки контрасту

---

## 🚀 Переваги нового підходу

1. **Відсутність помилок Excel** - гарантовано коректні назви аркушів
2. **Високий контраст** - всі елементи добре видимі на темному фоні
3. **Компактність** - один прогрес-бар замість десятків смуг
4. **Централізація** - всі кольори в одному місці
5. **Читабельність** - зрозумілі повідомлення без "кракозябр"
6. **Професійність** - структурований та зрозумілий вигляд

---

**Автор:** sverdz
**Дата:** 2024-11-07
**Гілка:** `claude/file-rename-formatting-logic-011CUsbsveaiMLaAXT1vEpFj`
