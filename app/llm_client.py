"""LLM client for document analysis."""
from __future__ import annotations

import json
from typing import Dict, Optional, Tuple
import requests
from rich.console import Console

console = Console()


class LLMClient:
    """Клієнт для роботи з LLM API (Claude та ChatGPT)."""

    def __init__(
        self,
        provider: str,
        api_key: str,
        model: str,
        enabled: bool = False,
    ):
        self.provider = provider
        self.api_key = api_key
        self.model = model
        self.enabled = enabled
        self.request_count = 0
        self.response_count = 0
        self.total_tokens = 0
        self.tokens_sent = 0
        self.tokens_received = 0

    def analyze_document(
        self, text: str, max_length: int = 2000
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Аналізувати документ за допомогою LLM.

        Returns:
            (category, date, summary) або (None, None, None) якщо LLM вимкнено
        """
        if not self.enabled or not self.api_key:
            return None, None, None

        # Обрізаємо текст до розумного розміру
        text_sample = text[:max_length] if len(text) > max_length else text

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
                    summary = data.get("summary")

                    return category, date, summary
                except json.JSONDecodeError as e:
                    # Тільки при помилці виводимо в консоль
                    console.print(
                        f"[yellow]⚠ Помилка парсингу JSON від LLM: {e}[/yellow]"
                    )
                    return None, None, None
            else:
                return None, None, None

        except Exception as e:
            # Тільки при помилці виводимо в консоль
            console.print(f"[yellow]⚠ Помилка LLM запиту: {e}[/yellow]")
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


__all__ = ["LLMClient"]
