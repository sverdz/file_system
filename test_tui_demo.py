#!/usr/bin/env python3
"""Демонстрація нового TUI dashboard."""
import time
from app.live_tui import LiveTUI

def demo_tui():
    """Демонстрація TUI з симуляцією обробки файлів."""
    tui = LiveTUI()

    # Встановити інформацію про запуск
    tui.set_run_info(
        run_id="2025-11-07_17-20-03",
        root_path="D:\\DATA\\ARCHIVE\\"
    )

    # Запустити TUI
    tui.start(total_files=5)

    # Встановити totals для етапів
    tui.set_stage_totals({
        "scan": 5,
        "dedup": 5,
        "extract": 5,
        "classify": 5,
        "rename": 5,
        "inventory": 5,
    })

    try:
        # Файл 1 - Успіх
        tui.start_file(
            "document_2024.pdf",
            size_bytes=2500000,
            modified_time=time.time(),
            sha256="a3f5c9d2e1b8f4a6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0"
        )
        tui.update_current_file_stage("scan", 100)
        time.sleep(0.5)
        tui.update_stage_progress("scan", completed=1)

        tui.update_current_file_stage("dedup", 100)
        time.sleep(0.5)
        tui.update_stage_progress("dedup", completed=1)

        tui.update_current_file_stage("extract", 100)
        time.sleep(0.5)
        tui.update_stage_progress("extract", completed=1)

        tui.update_current_file_stage("classify", 100)
        tui.update_current_file_note("Фінансовий звіт за 2024 рік")
        time.sleep(0.5)
        tui.update_stage_progress("classify", completed=1)

        tui.update_current_file_category("Фінансові документи")
        tui.update_current_file_stage("rename", 100)
        tui.update_current_file_stage("inventory", 100)
        time.sleep(0.5)

        tui.update_metrics(success_count=1, llm_requests=1, llm_responses=1)
        tui.finish_file(
            status="success",
            category="Фінансові документи",
            message="Фінансовий звіт за 2024 рік, містить баланс та рух коштів"
        )

        # Файл 2 - Успіх
        time.sleep(0.3)
        tui.start_file(
            "contract_signed.docx",
            size_bytes=856000,
            modified_time=time.time(),
            sha256="b7e2d1c4a5f6b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3"
        )
        tui.update_current_file_stage("scan", 100)
        tui.update_current_file_stage("dedup", 100)
        tui.update_current_file_stage("extract", 100)
        tui.update_current_file_stage("classify", 100)
        tui.update_current_file_note("Договір про надання послуг")
        time.sleep(0.5)

        tui.update_metrics(success_count=2, llm_requests=2, llm_responses=2)
        tui.finish_file(
            status="success",
            category="Юридичні документи",
            message="Договір про надання послуг між ТОВ та ФОП"
        )

        # Файл 3 - Попередження (дублікат)
        time.sleep(0.3)
        tui.start_file(
            "report_Q4.xlsx",
            size_bytes=1200000,
            modified_time=time.time(),
            sha256="d4a8f3e2c1b0a9f8e7d6c5b4a3f2e1d0c9b8a7f6e5d4c3b2a1f0e9d8c7b6a5f4"
        )
        tui.update_current_file_stage("scan", 100)
        tui.update_current_file_stage("dedup", 100)
        time.sleep(0.5)

        tui.update_metrics(duplicate_groups=1, duplicate_files=1)
        tui.finish_file(
            status="warning",
            message="DUP MATCH → report_Q4_final.xlsx (100%)"
        )

        # Файл 4 - Помилка
        time.sleep(0.3)
        tui.start_file(
            "scan_001.jpg",
            size_bytes=3200000,
            modified_time=time.time(),
            sha256="e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6"
        )
        tui.update_current_file_stage("scan", 100)
        tui.update_current_file_stage("dedup", 100)
        tui.update_current_file_stage("extract", 50)
        time.sleep(0.5)

        tui.add_error(
            filename="scan_001.jpg",
            stage="extract",
            error_message="OCR failed: Image quality too low",
            traceback="pytesseract.TesseractError: Failed to read image\n  File: /usr/lib/python3/tesseract.py line 234"
        )
        tui.finish_file(
            status="error",
            message="OCR failed: Image quality too low",
            error_details="pytesseract.TesseractError: Failed to read image"
        )

        # Файл 5 - Успіх
        time.sleep(0.3)
        tui.start_file(
            "invoice_2024_003.pdf",
            size_bytes=445000,
            modified_time=time.time(),
            sha256="c9f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1"
        )
        tui.update_current_file_stage("scan", 100)
        tui.update_current_file_stage("dedup", 100)
        tui.update_current_file_stage("extract", 100)
        tui.update_current_file_stage("classify", 100)
        time.sleep(0.5)

        tui.update_metrics(success_count=3, llm_requests=3, llm_responses=3)
        tui.finish_file(
            status="success",
            category="Фінансові документи",
            message="Рахунок-фактура на суму 15,780 грн за послуги"
        )

        # Фінальне оновлення статистики
        tui.update_metrics(
            total_size_bytes=8200000,
            output_size_bytes=8100000,
            shrinkage=1.2,
            avg_time=2.4,
            speed=2.1,
            ocr_files=1,
        )

        # Почекати щоб побачити результат
        time.sleep(5)

    finally:
        tui.stop()

    print("\n✅ Демо завершено! TUI працює коректно.")
    print(f"📊 Оброблено файлів: {tui.files_processed}")
    print(f"✅ Успішно: {tui.metrics.success_count}")
    print(f"⚠️  Попередження: {tui.metrics.duplicate_groups}")
    print(f"❌ Помилки: {tui.metrics.error_count}")

if __name__ == "__main__":
    demo_tui()
