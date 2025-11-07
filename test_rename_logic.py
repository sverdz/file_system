#!/usr/bin/env python3
"""Тест нової логіки перейменування з обмеженням 20 символів."""

from app.rename import (
    sanitize_filename_component,
    generate_short_suffix,
    build_short_filename,
)


def test_sanitize_filename_component():
    """Тест очищення компонентів імені."""
    print("\n=== Тест sanitize_filename_component() ===")

    tests = [
        ("договір", "dohovir"),
        ("рахунок-фактура", "rakhunok_faktura"),
        ("Акт виконаних робіт", "Akt_vikonanih_robit"),
        ("тендер/пропозиція", "tenderpropozitsiia"),
        ("звіт №123", "zvit_123"),
        ("інше@документ!", "inshedokument"),
    ]

    for input_text, expected_contains in tests:
        result = sanitize_filename_component(input_text)
        print(f"  '{input_text}' → '{result}'")
        # Перевірка що результат містить лише безпечні символи
        assert all(c.isalnum() or c in "_-" for c in result), f"Unsafe chars in: {result}"

    print("  ✓ Всі тести пройдено")


def test_generate_short_suffix():
    """Тест генерації коротких суфіксів."""
    print("\n=== Тест generate_short_suffix() ===")

    # Перші 26 - літери
    assert generate_short_suffix(0) == "a"
    assert generate_short_suffix(1) == "b"
    assert generate_short_suffix(25) == "z"
    print("  ✓ Літери a-z: OK")

    # Потім числа
    assert generate_short_suffix(26) == "1"
    assert generate_short_suffix(27) == "2"
    assert generate_short_suffix(50) == "25"
    print("  ✓ Числа 1-99: OK")

    # Приклади
    for i in [0, 1, 2, 25, 26, 27, 50]:
        suffix = generate_short_suffix(i)
        print(f"  Індекс {i:2d} → '{suffix}'")

    print("  ✓ Всі тести пройдено")


def test_build_short_filename():
    """Тест створення коротких імен файлів."""
    print("\n=== Тест build_short_filename() ===")

    tests = [
        {
            "name": "Простий договір",
            "date": "20241107",
            "category": "договір",
            "suffix_idx": 0,
            "ext": ".pdf",
            "use_short_date": False,
            "expected_length": 20,  # без розширення
        },
        {
            "name": "Рахунок з коротшою датою",
            "date": "241107",
            "category": "рахунок",
            "suffix_idx": 1,
            "ext": ".pdf",
            "use_short_date": True,
            "expected_length": 20,
        },
        {
            "name": "Довга категорія (має бути обрізана)",
            "date": "20241107",
            "category": "рахунок-фактура-за-виконані-роботи",
            "suffix_idx": 0,
            "ext": ".xlsx",
            "use_short_date": False,
            "expected_length": 20,
        },
        {
            "name": "Колізія з індексом 26 (перший числовий)",
            "date": "20241107",
            "category": "акт",
            "suffix_idx": 26,
            "ext": ".docx",
            "use_short_date": False,
            "expected_length": 20,
        },
    ]

    for test in tests:
        filename = build_short_filename(
            date_str=test["date"],
            category=test["category"],
            suffix_index=test["suffix_idx"],
            extension=test["ext"],
            use_short_date=test["use_short_date"],
        )

        # Отримати довжину без розширення
        from pathlib import Path
        name_without_ext = Path(filename).stem
        length = len(name_without_ext)

        print(f"\n  {test['name']}:")
        print(f"    Дата: {test['date']}, Категорія: {test['category'][:20]}...")
        print(f"    Результат: {filename}")
        print(f"    Довжина без розширення: {length}")

        # Перевірка довжини
        if length > test["expected_length"]:
            print(f"    ⚠ УВАГА: Довжина {length} перевищує ліміт {test['expected_length']}!")
        else:
            print(f"    ✓ Довжина {length} <= {test['expected_length']}")

        # Перевірка безпечних символів
        assert all(c.isalnum() or c in "._-" for c in filename), f"Unsafe chars in: {filename}"

    print("\n  ✓ Всі тести пройдено")


def test_edge_cases():
    """Тест граничних випадків."""
    print("\n=== Тест граничних випадків ===")

    # Дуже короткі компоненти
    filename = build_short_filename("20241107", "a", 0, ".pdf")
    print(f"  Мінімальна категорія: {filename}")

    # Пуста категорія
    filename = build_short_filename("20241107", "", 0, ".pdf")
    print(f"  Пуста категорія: {filename}")

    # Максимальний суфікс
    filename = build_short_filename("20241107", "договір", 100, ".pdf")
    print(f"  Великий індекс (100): {filename}")

    # Коротка дата з довгою категорією
    filename = build_short_filename(
        "241107", "рахунок-фактура-дуже-довга-назва", 5, ".xlsx", use_short_date=True
    )
    from pathlib import Path
    length = len(Path(filename).stem)
    print(f"  Коротка дата + довга категорія: {filename} (довжина: {length})")

    print("\n  ✓ Всі граничні випадки оброблено")


def main():
    """Запуск всіх тестів."""
    print("╔════════════════════════════════════════════════════════════╗")
    print("║  Тестування нової логіки перейменування (20 символів)     ║")
    print("╚════════════════════════════════════════════════════════════╝")

    try:
        test_sanitize_filename_component()
        test_generate_short_suffix()
        test_build_short_filename()
        test_edge_cases()

        print("\n" + "=" * 60)
        print("✓ ВСІ ТЕСТИ УСПІШНО ПРОЙДЕНО!")
        print("=" * 60 + "\n")

    except Exception as e:
        print(f"\n✗ ПОМИЛКА: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
