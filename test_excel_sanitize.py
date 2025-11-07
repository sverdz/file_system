#!/usr/bin/env python3
"""Тест санітизації для Excel."""
from app.inventory import sanitize_cell_value

def test_sanitize():
    """Тест функції sanitize_cell_value."""

    print("🧪 Тест санітизації Excel...")
    print()

    # Тест 1: Контрольні символи
    test1 = "АНДРІЙ ГРИЦЕВИЧ Активний\x00\x01\x02креативний"
    result1 = sanitize_cell_value(test1)
    print("✅ Тест 1: Контрольні символи")
    print(f"   Вхід:  {repr(test1)}")
    print(f"   Вихід: {repr(result1)}")
    assert '\x00' not in result1
    assert '\x01' not in result1
    assert '\x02' not in result1
    print()

    # Тест 2: Довгий текст (проблемний з помилки)
    test2 = 'АНДРІЙ ГРИЦЕВИЧ Активний, креативний та цлеспрямований. Органзатор та учасник численних\nпроектв. Отримую натхнення та задоволення вд втлення дей. Життєве кредо "Краще вигорти вщент, нж згаснут"'
    result2 = sanitize_cell_value(test2)
    print("✅ Тест 2: Звичайний текст з \n")
    print(f"   Довжина: {len(result2)} символів")
    print(f"   Вихід: {result2[:100]}...")
    assert len(result2) <= 32767
    print()

    # Тест 3: Дуже довгий текст
    test3 = "A" * 40000
    result3 = sanitize_cell_value(test3)
    print("✅ Тест 3: Дуже довгий текст")
    print(f"   Вхідна довжина: {len(test3)}")
    print(f"   Вихідна довжина: {len(result3)}")
    assert len(result3) <= 32767
    assert result3.endswith("...")
    print()

    # Тест 4: Не-строкові значення
    test4_num = 42
    test4_none = None
    test4_float = 3.14
    print("✅ Тест 4: Не-строкові значення")
    print(f"   Число: {sanitize_cell_value(test4_num)} (тип: {type(sanitize_cell_value(test4_num)).__name__})")
    print(f"   None: {sanitize_cell_value(test4_none)}")
    print(f"   Float: {sanitize_cell_value(test4_float)} (тип: {type(sanitize_cell_value(test4_float)).__name__})")
    assert sanitize_cell_value(test4_num) == 42
    assert sanitize_cell_value(test4_none) is None
    assert sanitize_cell_value(test4_float) == 3.14
    print()

    # Тест 5: Всі контрольні символи ASCII 0-31
    control_chars = ''.join(chr(i) for i in range(32))
    test5 = f"Текст{control_chars}після"
    result5 = sanitize_cell_value(test5)
    print("✅ Тест 5: Всі контрольні символи ASCII 0-31")
    print(f"   Вхід містить: {len([c for c in test5 if ord(c) < 32])} контрольних символів")
    # TAB (9), LF (10), CR (13) мають залишитись
    remaining_control = len([c for c in result5 if ord(c) < 32])
    print(f"   Вихід містить: {remaining_control} контрольних символів (TAB, LF, CR)")
    print(f"   Результат: '{result5}'")
    print()

    # Тест 6: Реальний приклад з помилки
    test6 = '''АНДРІЙ ГРИЦЕВИЧ Активний, креативний та цлеспрямований. Органзатор та учасник численних
проектв. Отримую натхнення та задоволення вд втлення дей. Життєве кредо "Краще вигорти вщент, нж згаснут"'''
    result6 = sanitize_cell_value(test6)
    print("✅ Тест 6: Реальний приклад з помилки")
    print(f"   Текст коректно оброблено: {len(result6)} символів")
    print(f"   Перші 100 символів: {result6[:100]}")
    assert "АНДРІЙ ГРИЦЕВИЧ" in result6
    assert "Активний" in result6
    print()

    print("=" * 60)
    print("🎉 Всі тести пройдено успішно!")
    print("=" * 60)

if __name__ == "__main__":
    test_sanitize()
