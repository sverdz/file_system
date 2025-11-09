#!/usr/bin/env python3
"""Тестовий скрипт для перевірки відображення великих масивів файлів у TUI."""
import time
import random
from pathlib import Path

# Імпортуємо необхідні модулі
try:
    from app.live_tui import LiveDashboard
    from rich.console import Console
except ImportError as e:
    print(f"Помилка імпорту: {e}")
    print("Переконайтеся що всі залежності встановлені: pip install -r requirements.txt")
    exit(1)


def test_large_file_array():
    """Тестує TUI з великою кількістю файлів."""

    console = Console()
    dashboard = LiveDashboard(console)

    # Симулюємо обробку 1000 файлів
    total_files = 1000

    # Встановлюємо загальну кількість
    dashboard.start(total_files)

    # Симулюємо стадії
    stages = {
        "scan": total_files,
        "dedup": total_files,
        "extract": total_files,
        "classify": total_files,
        "rename": total_files,
        "inventory": total_files,
    }
    dashboard.set_stage_totals(stages)

    try:
        # Обробляємо файли
        categories = ["договір", "рахунок", "акт", "протокол", "лист", "наказ", "звіт", "unknown"]

        for i in range(total_files):
            # Початок обробки файлу
            filename = f"document_{i:04d}.pdf"
            size = random.randint(10_000, 5_000_000)

            dashboard.begin_file(
                filename=filename,
                size_bytes=size,
                modified_time=time.time() - random.randint(0, 365*24*3600)
            )

            # Симулюємо проходження стадій
            for stage_idx, stage_name in enumerate(stages.keys()):
                dashboard.update_stage_progress(stage_name, i + 1)
                dashboard.set_current_stage(stage_name)

                # Симулюємо час обробки
                time.sleep(0.001)

                # Оновлюємо прогрес поточного файлу
                progress = (stage_idx + 1) / len(stages) * 100
                dashboard.current_file.stage_progress[stage_name] = progress

            # Визначаємо статус (більшість успішні)
            status = "success"
            category = random.choice(categories)
            message = ""

            # Іноді помилки
            if random.random() < 0.05:  # 5% помилок
                status = "error"
                message = f"Помилка обробки файлу {filename}"
                dashboard.update_metrics(error_count=dashboard.metrics.error_count + 1)
            # Іноді попередження
            elif random.random() < 0.10:  # 10% попереджень
                status = "warning"
                message = f"Можливий дублікат"
            else:
                dashboard.update_metrics(success_count=dashboard.metrics.success_count + 1)

            # Завершуємо обробку файлу
            dashboard.process_file(
                status=status,
                filename=filename,
                message=message,
                category=category,
                size_bytes=size
            )

            # Оновлюємо метрики
            if i % 10 == 0:
                dashboard.update_metrics(
                    total_size_bytes=dashboard.metrics.total_size_bytes + size,
                )

                # Оновлюємо швидкість
                elapsed = time.time() - dashboard.start_time
                if elapsed > 0:
                    speed = (i + 1) / elapsed
                    dashboard.update_speed(speed)

                    # Оновлюємо ETA
                    remaining = total_files - (i + 1)
                    eta = remaining / speed if speed > 0 else 0
                    dashboard.update_eta(eta)

            # Затримка для візуалізації (можна прибрати для швидшого тесту)
            if i < 100:  # Перші 100 файлів показуємо повільно
                time.sleep(0.05)
            elif i % 50 == 0:  # Потім кожен 50-й
                time.sleep(0.1)

        # Тримаємо дисплей відкритим кілька секунд після завершення
        console.print("\n[green]✓ Обробка завершена! Натисніть Ctrl+C для виходу[/green]")
        time.sleep(5)

    except KeyboardInterrupt:
        console.print("\n[yellow]Перервано користувачем[/yellow]")

    finally:
        dashboard.stop()

        # Виводимо статистику
        console.print("\n" + "="*60)
        console.print("[bold cyan]СТАТИСТИКА ТЕСТУ:[/bold cyan]")
        console.print(f"  Всього файлів: {total_files}")
        console.print(f"  Успішно оброблено: {dashboard.metrics.success_count}")
        console.print(f"  Помилок: {dashboard.metrics.error_count}")
        console.print(f"  Файлів в логу: {len(dashboard.file_log)}")
        console.print(f"  Помилок в логу: {len(dashboard.error_log)}")
        console.print(f"  Обробка файлів (лічильник): {dashboard.files_processed}")
        console.print("="*60)

        # Перевірка лімітів
        if len(dashboard.file_log) > 500:
            console.print(f"[red]❌ ПОМИЛКА: Лог файлів перевищує 500: {len(dashboard.file_log)}[/red]")
        else:
            console.print(f"[green]✓ Лог файлів в межах ліміту: {len(dashboard.file_log)}/500[/green]")

        if len(dashboard.error_log) > 100:
            console.print(f"[red]❌ ПОМИЛКА: Лог помилок перевищує 100: {len(dashboard.error_log)}[/red]")
        else:
            console.print(f"[green]✓ Лог помилок в межах ліміту: {len(dashboard.error_log)}/100[/green]")


if __name__ == "__main__":
    print("Тестування TUI з великим масивом файлів (1000 файлів)")
    print("="*60)
    test_large_file_array()
