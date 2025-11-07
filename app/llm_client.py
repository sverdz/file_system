"""LLM client for document analysis."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import requests
from rich.console import Console

console = Console()


class LLMClient:
    """Клієнт для роботи з LLM API (Claude та ChatGPT)."""

    # Ліміти для запитів/відповідей
    MAX_INPUT_LENGTH = 1000  # Максимум символів на вхід
    MAX_OUTPUT_DISPLAY = 500  # Максимум символів для відображення в TUI

    def __init__(
        self,
        provider: str,
        api_key: str,
        model: str,
        enabled: bool = False,
        session_dir: Optional[Path] = None,
    ):
        self.provider = provider
        self.api_key = api_key
        self.model = model
        self.enabled = enabled
        self.session_dir = session_dir
        self.request_count = 0
        self.response_count = 0
        self.total_tokens = 0
        self.tokens_sent = 0
        self.tokens_received = 0

        # Лог всіх запитів/відповідей для сесії
        self.request_log: List[Dict] = []

    def analyze_document(
        self, text: str, filename: str = ""
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Аналізувати документ за допомогою LLM.

        Args:
            text: Текст документа
            filename: Назва файлу (для логування)

        Returns:
            (category, date, summary) або (None, None, None) якщо LLM вимкнено
        """
        if not self.enabled or not self.api_key:
            return None, None, None

        # Обрізаємо текст до встановленого ліміту (1000 символів)
        text_sample = text[:self.MAX_INPUT_LENGTH] if len(text) > self.MAX_INPUT_LENGTH else text

        prompt = f"""Проаналізуй цей документ і дай відповідь у форматі JSON:

Текст документа:
{text_sample}

Відповідь має містити:
1. "category" - тип документа (договір, рахунок, акт, протокол, лист, наказ, звіт, кошторис, тендер, презентація, довідка, ТЗ, специфікація, або інше)
2. "date" - дата документа у форматі YYYY-MM-DD (якщо є)
3. "summary" - короткий опис документа (2-3 речення, максимум 200 символів)

Відповідай тільки валідним JSON без додаткового тексту."""

        try:
            response_text = self._make_request(prompt)

            if response_text:
                self.response_count += 1

                # Парсимо JSON
                try:
                    # Видаляємо можливі markdown backticks
                    clean_response = response_text.strip()
                    if clean_response.startswith("```"):
                        clean_response = clean_response.split("```")[1]
                        if clean_response.startswith("json"):
                            clean_response = clean_response[4:]
                    clean_response = clean_response.strip()

                    data = json.loads(clean_response)
                    category = data.get("category")
                    date = data.get("date")
                    summary_full = data.get("summary", "")

                    # Обрізаємо summary до ліміту для відображення (500 символів)
                    summary_display = summary_full[:self.MAX_OUTPUT_DISPLAY] if len(summary_full) > self.MAX_OUTPUT_DISPLAY else summary_full

                    # Логуємо запит/відповідь
                    self._log_request(
                        filename=filename,
                        input_text=text_sample,
                        category=category,
                        date=date,
                        summary_full=summary_full,
                        summary_display=summary_display,
                    )

                    return category, date, summary_display
                except json.JSONDecodeError as e:
                    # Тільки при помилці виводимо в консоль
                    console.print(
                        f"[yellow]⚠ Помилка парсингу JSON від LLM: {e}[/yellow]"
                    )
                    self._log_request(
                        filename=filename,
                        input_text=text_sample,
                        error=str(e),
                    )
                    return None, None, None
            else:
                return None, None, None

        except Exception as e:
            # Тільки при помилці виводимо в консоль
            console.print(f"[yellow]⚠ Помилка LLM запиту: {e}[/yellow]")
            self._log_request(
                filename=filename,
                input_text=text_sample,
                error=str(e),
            )
            return None, None, None

    def _make_request(self, prompt: str) -> Optional[str]:
        """Виконати запит до LLM API."""
        self.request_count += 1

        try:
            if self.provider == "claude":
                return self._request_claude(prompt)
            elif self.provider == "chatgpt":
                return self._request_openai(prompt)
            else:
                return None
        except Exception as e:
            console.print(f"[red]Помилка запиту до {self.provider}: {e}[/red]")
            return None

    def _request_claude(self, prompt: str) -> Optional[str]:
        """Запит до Claude API."""
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        data = {
            "model": self.model or "claude-3-haiku-20240307",
            "max_tokens": 500,
            "messages": [{"role": "user", "content": prompt}],
        }

        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            json=data,
            timeout=30,
        )

        if response.status_code == 200:
            result = response.json()
            # Оновлюємо статистику
            if "usage" in result:
                input_tokens = result["usage"].get("input_tokens", 0)
                output_tokens = result["usage"].get("output_tokens", 0)
                self.tokens_sent += input_tokens
                self.tokens_received += output_tokens
                self.total_tokens += input_tokens + output_tokens

            content = result.get("content", [])
            if content and len(content) > 0:
                return content[0].get("text", "")
        else:
            console.print(
                f"[red]Claude API помилка {response.status_code}: {response.text}[/red]"
            )

        return None

    def _request_openai(self, prompt: str) -> Optional[str]:
        """Запит до OpenAI API."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        data = {
            "model": self.model or "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 500,
            "temperature": 0.3,
        }

        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=data,
            timeout=30,
        )

        if response.status_code == 200:
            result = response.json()
            # Оновлюємо статистику
            if "usage" in result:
                prompt_tokens = result["usage"].get("prompt_tokens", 0)
                completion_tokens = result["usage"].get("completion_tokens", 0)
                self.tokens_sent += prompt_tokens
                self.tokens_received += completion_tokens
                self.total_tokens += result["usage"].get("total_tokens", 0)

            choices = result.get("choices", [])
            if choices and len(choices) > 0:
                return choices[0].get("message", {}).get("content", "")
        else:
            console.print(
                f"[red]OpenAI API помилка {response.status_code}: {response.text}[/red]"
            )

        return None

    def get_stats(self) -> Dict[str, int]:
        """Отримати статистику використання."""
        return {
            "requests": self.request_count,
            "responses": self.response_count,
            "tokens": self.total_tokens,
            "tokens_sent": self.tokens_sent,
            "tokens_received": self.tokens_received,
        }

    def _log_request(
        self,
        filename: str,
        input_text: str,
        category: Optional[str] = None,
        date: Optional[str] = None,
        summary_full: Optional[str] = None,
        summary_display: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        """
        Залогувати запит/відповідь для подальшого збереження.

        Args:
            filename: Назва файлу
            input_text: Вхідний текст (обрізаний до MAX_INPUT_LENGTH)
            category: Категорія з відповіді
            date: Дата з відповіді
            summary_full: Повний summary (необрізаний)
            summary_display: Summary для відображення (обрізаний до MAX_OUTPUT_DISPLAY)
            error: Помилка (якщо була)
        """
        from datetime import datetime, timezone

        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "filename": filename,
            "provider": self.provider,
            "model": self.model,
            "input_length": len(input_text),
            "input_text": input_text,
            "category": category,
            "date": date,
            "summary_full": summary_full,
            "summary_display": summary_display,
            "summary_truncated": len(summary_full or "") > self.MAX_OUTPUT_DISPLAY if summary_full else False,
            "error": error,
            "request_number": self.request_count,
        }

        self.request_log.append(log_entry)

    def save_log_to_file(self, session_dir: Path) -> Optional[Path]:
        """
        Зберегти лог запитів/відповідей у файл сесії.

        Args:
            session_dir: Директорія сесії

        Returns:
            Path до створеного файлу або None
        """
        if not self.request_log:
            return None

        log_path = session_dir / "llm_full_log.json"

        try:
            log_data = {
                "provider": self.provider,
                "model": self.model,
                "limits": {
                    "max_input_length": self.MAX_INPUT_LENGTH,
                    "max_output_display": self.MAX_OUTPUT_DISPLAY,
                },
                "statistics": self.get_stats(),
                "requests": self.request_log,
            }

            log_path.write_text(
                json.dumps(log_data, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )

            return log_path
        except Exception as e:
            console.print(f"[yellow]⚠ Помилка збереження LLM логу: {e}[/yellow]")
            return None


__all__ = ["LLMClient"]
