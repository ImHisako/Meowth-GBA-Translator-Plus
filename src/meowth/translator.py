"""Validated translation with local caching: DeepL and OpenAI-compatible APIs."""

import hashlib
import json
import os
import math
import random
import re
import sys
import threading
import time
import tempfile
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape
from pathlib import Path
from concurrent.futures import CancelledError
from email.utils import parsedate_to_datetime
from typing import Callable

import httpx

from .languages import get_language_name, get_language_name_zh
from .translation_validation import TOKEN_RE, TranslationValidationError, validate_translation


def _get_default_cache_dir() -> Path:
    """Get default cache directory — writable in both dev and PyInstaller bundle."""
    if getattr(sys, '_MEIPASS', None):  # Running in PyInstaller bundle
        if sys.platform == "darwin":
            return Path.home() / "Library" / "Caches" / "Meowth" / "work" / "cache"
        elif sys.platform == "win32":
            return Path.home() / "AppData" / "Local" / "Meowth" / "Cache" / "work" / "cache"
        else:
            return Path.home() / ".cache" / "Meowth" / "work" / "cache"
    # Dev / CLI: use work/cache relative to project root
    return Path(__file__).parent.parent.parent / "work" / "cache"


DEFAULT_CACHE_DIR = _get_default_cache_dir()

# Well-known provider presets: provider_name -> (base_url, default_model, env_var)
PROVIDER_PRESETS: dict[str, tuple[str, str, str]] = {
    "deepl":     ("", "", "DEEPL_API_KEY"),
    "deepseek":  ("https://api.deepseek.com/v1",              "deepseek-chat",     "DEEPSEEK_API_KEY"),
    "openai":    ("https://api.openai.com/v1",                 "gpt-4o",            "OPENAI_API_KEY"),
    "anthropic": ("https://api.anthropic.com/v1",              "claude-sonnet-4-20250514", "ANTHROPIC_API_KEY"),
    "google":    ("https://generativelanguage.googleapis.com/v1beta/openai", "gemini-2.0-flash", "GOOGLE_API_KEY"),
    "groq":      ("https://api.groq.com/openai/v1",           "llama-3.3-70b-versatile", "GROQ_API_KEY"),
    "mistral":   ("https://api.mistral.ai/v1",                "mistral-large-latest", "MISTRAL_API_KEY"),
    "openrouter":("https://openrouter.ai/api/v1",             "openai/gpt-4o",     "OPENROUTER_API_KEY"),
    "siliconflow":("https://api.siliconflow.cn/v1",           "deepseek-ai/DeepSeek-V3", "SILICONFLOW_API_KEY"),
    "zhipu":     ("https://open.bigmodel.cn/api/paas/v4",     "glm-4-flash",       "ZHIPU_API_KEY"),
    "moonshot":  ("https://api.moonshot.cn/v1",               "moonshot-v1-8k",    "MOONSHOT_API_KEY"),
    "qwen":      ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus", "DASHSCOPE_API_KEY"),
}

# Language-specific prompt templates
PROMPT_TEMPLATES = {
    "zh-Hans": {
        "system": """你是一个专业的宝可梦游戏本地化翻译专家。请将以下宝可梦游戏文本从{source_lang}翻译成简体中文。

核心规则：
1. 控制码占位符（如 {{C0}}, {{C1}} 等）必须原样保留，不得修改、删除或增加
   - 这些是游戏的控制码（换行、翻页、颜色等），改动会导致游戏崩溃
2. 占位符的数量和顺序必须与原文完全一致
3. 使用宝可梦官方简体中文译名（皮卡丘、小火龙、妙蛙种子等）
4. POKéMON / Pokémon 翻译为"宝可梦"
5. 保持游戏对话的自然口语风格
6. 人名地名等专有名词如果有官方译名则使用官方译名，否则音译
7. 只返回翻译结果，不要任何解释或注释
8. 如果输入文本中没有任何可翻译的内容（纯符号或乱码），请原封不动地返回原文
9. 翻译时不要插入任何换行符，输出纯文本即可，系统会自动排版
10. 保留所有 \\. 等待标记的位置，它们表示游戏中的停顿效果
11. 保留段落分隔（空行），它们表示游戏中的翻页

重要：占位符代表游戏运行时会替换的变量（如玩家名、劲敌名等），翻译时不要用人名替代周围的 rival 等词。
- "rival" 一词翻译为"劲敌"，不要翻译为具体人名（如小茂）
- 例如 "your rival {{C0}}" 应翻译为 "你的劲敌{{C0}}"，而不是 "小茂{{C0}}"

重要：你必须将所有英文内容翻译成中文。不要原样返回英文文本。即使是地名、专有名词也要翻译或音译。

术语表：
{glossary}""",
        "user": """请将以下宝可梦游戏文本从{source_lang}翻译成简体中文。
每条文本用 ||| 分隔，请按相同顺序返回翻译结果，也用 ||| 分隔。
不要添加编号或额外说明，只返回翻译后的文本。

{texts}""",
    },
    "generic": {
        "system": """You are a professional Pokemon game localization expert. Translate the following Pokemon game text from {source_lang} to {target_lang}.

Core rules:
1. Control code placeholders (like {{C0}}, {{C1}}, etc.) MUST be preserved exactly - do not modify, delete, or add any
   - These are game control codes (line breaks, page breaks, colors, etc.) and changing them will crash the game
2. The number and order of placeholders must match the original text exactly
3. Use official Pokemon terminology from the glossary provided
4. Maintain the natural conversational style of game dialogue
5. For proper nouns (character names, place names), use official translations if available in the glossary, otherwise transliterate
6. Return only the translation, no explanations or notes
7. If the input contains no translatable content (pure symbols or gibberish), return it unchanged
8. Do not insert any line breaks in your translation - output plain text, the system will handle formatting
9. Preserve all \\. pause markers, they represent in-game pauses
10. Preserve paragraph breaks (blank lines), they represent page breaks in the game

Important: Placeholders represent variables that will be replaced at runtime (player name, rival name, etc.). Do not replace words like "rival" with specific names.
- For example, "your rival {{C0}}" should be translated preserving the word "rival" in {target_lang}, not replaced with a specific character name

Terminology glossary:
{glossary}""",
        "user": """Translate the following Pokemon game text from {source_lang} to {target_lang}.
Each text is separated by |||. Return translations in the same order, also separated by |||.
Do not add numbering or extra explanations, only return the translated text.

{texts}""",
    },
}


class DeepLQuotaExceededError(httpx.HTTPStatusError):
    """Terminal quota exhaustion with recovery instructions for GUI and CLI."""

    def __init__(self, response: httpx.Response, cache_dir: Path, key_count: int = 1):
        limit = (
            "DeepL API Free: quota mensile di 500.000 caratteri esaurita. "
            "Attendi il rinnovo della quota o valuta un piano API Pro. "
            if response.request.url.host == "api-free.deepl.com" else
            "DeepL: limite di caratteri raggiunto. Controlla il limite di spesa "
            "e il limite della chiave API nel tuo account. "
        )
        exhausted = f"Tutte le {key_count} chiavi DeepL hanno esaurito la quota. " if key_count > 1 else ""
        super().__init__(
            "Quota DeepL esaurita (HTTP 456). " + exhausted + limit +
            "Le pause e i tentativi automatici non risolvono questo limite. "
            f"Le traduzioni già salvate sono conservate in {cache_dir.resolve()}. "
            "Quando la quota sarà disponibile, rilancia con le stesse impostazioni "
            "per riutilizzare la cache. Non cancellare questa cartella.",
            request=response.request, response=response,
        )


class Translator:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        cache_dir: Path = DEFAULT_CACHE_DIR,
        source_lang: str = "en",
        target_lang: str = "zh-Hans",
        base_url: str | None = None,
        api_key_env: str | None = None,
        provider: str | None = None,
        cancel_event: threading.Event | None = None,
        on_log: Callable[[str, str], None] | None = None,
        on_retry_wait: Callable[[float], None] | None = None,
    ):
        self.provider = (provider or "deepseek").lower()
        provider = self.provider
        # Resolve provider preset
        if provider and provider in PROVIDER_PRESETS:
            preset_url, preset_model, preset_env = PROVIDER_PRESETS[provider]
            base_url = base_url or preset_url
            model = model or preset_model
            api_key_env = api_key_env or preset_env

        # Defaults (backward compatible with DeepSeek)
        self.base_url = base_url or "https://api.deepseek.com/v1"
        self.model = model or "deepseek-chat"
        env_var = api_key_env or "DEEPSEEK_API_KEY"
        self.api_key = api_key or os.environ.get(env_var, "")
        if provider == "deepl":
            self._deepl_keys = list(dict.fromkeys(key.strip() for key in self.api_key.split(",") if key.strip()))
            self._deepl_key_index = 0
            self._deepl_quota_response = None
            self._deepl_custom_base_url = base_url
            self.api_key = self._deepl_keys[0] if self._deepl_keys else ""
            self.base_url = base_url or (
                "https://api-free.deepl.com/v2" if self.api_key.endswith(":fx")
                else "https://api.deepl.com/v2"
            )
            self.model = ""
        self.base_url = self.base_url.rstrip("/")
        # Keep existing cache keys stable when rotating between Free/Pro keys.
        self._cache_base_url = self.base_url
        self._client = httpx.Client(timeout=120.0)
        self._cancel_event = cancel_event if cancel_event is not None else threading.Event()
        self._on_log = on_log
        self._on_retry_wait = on_retry_wait
        # One shared gate for all batches using this translator. Hold it through
        # retries so other workers cannot send requests during a DeepL cooldown.
        self._request_lock = threading.Lock()
        self._request_interval = 1.0
        self._next_request_at = 0.0

        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_lock = threading.Lock()
        self.source_lang = source_lang
        self.target_lang = target_lang

        # Select appropriate prompt template and fill in language names
        source_name = get_language_name(source_lang)
        target_name = get_language_name(target_lang)
        template_key = target_lang if target_lang in PROMPT_TEMPLATES else "generic"
        # For Chinese template, use Chinese language names
        if template_key == "zh-Hans":
            source_name_local = get_language_name_zh(source_lang)
            target_name_local = get_language_name_zh(target_lang)
        else:
            source_name_local = source_name
            target_name_local = target_name
        self.prompts = {
            "system": PROMPT_TEMPLATES[template_key]["system"].replace(
                "{source_lang}", source_name_local
            ).replace("{target_lang}", target_name_local),
            "user": PROMPT_TEMPLATES[template_key]["user"].replace(
                "{source_lang}", source_name_local
            ).replace("{target_lang}", target_name_local),
        }
        self.prompts = {key: value.replace("{{", "{").replace("}}", "}")
                        for key, value in self.prompts.items()}
        self.prompts["system"] += (
            "\nPreserve every {P0}, {P1}, etc. Pokemon name placeholder exactly once; "
            "you may move these name placeholders to produce natural grammar. "
            "Preserve each {M0}, {M1}, etc. move-name placeholder exactly once too. "
            "These are official localized move names restored by the application."
        )
        if target_lang == "it":
            self.prompts["system"] += (
                "\nLocalizzazione italiana: usa un italiano naturale, chiaro e conciso, "
                "adatto ai dialoghi dei giochi Pokémon. Dai del tu al giocatore. "
                "Non tradurre mai i nomi delle specie Pokémon e non aggiungere plurali ai nomi. "
                "Usa Pokémon anche al plurale, Allenatore, lotta, mossa, abilità, "
                "strumento, PS, Palestra, Capopalestra e Centro Pokémon nel contesto di gioco. "
                "Rispetta la terminologia ufficiale del glossario, le negazioni, i numeri, "
                "il tono del personaggio e le informazioni necessarie al giocatore. "
                "Mantieni gli accenti italiani (è, é, à, ì, ò, ù); non copiare il maiuscolo "
                "inglese in intere frasi. Non attribuire un genere al giocatore quando non è noto."
            )

    def close(self):
        self._client.close()

    def _cache_key(self, request_data: dict) -> str:
        content = json.dumps(request_data, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(content.encode()).hexdigest()

    def _get_cached(self, key: str) -> str | None:
        with self._cache_lock:
            try:
                data = json.loads((self.cache_dir / f"{key}_response.json").read_text(encoding="utf-8"))
                return data.get("content") if isinstance(data, dict) else None
            except (OSError, ValueError):
                return None

    def _save_cache(self, key: str, content: str):
        # Atomic replacement also protects against interrupted writes and other workers.
        with self._cache_lock:
            name = None
            try:
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.cache_dir,
                                                 suffix=".tmp", delete=False) as output:
                    name = output.name
                    json.dump({"content": content}, output, ensure_ascii=False)
                os.replace(name, self.cache_dir / f"{key}_response.json")
            finally:
                if name:
                    Path(name).unlink(missing_ok=True)

    def translate_batch(self, texts: list[str], glossary_context: str = "") -> list[str]:
        """Deduplicate inputs and validate each response before returning or caching it."""
        resolved, missing, keys = {}, [], {}
        for text in dict.fromkeys(texts):
            if not text.strip() or not TOKEN_RE.sub("", text).strip():
                resolved[text] = text
                continue
            keys[text] = self._cache_key({
                "version": 3, "provider": self.provider, "base_url": self._cache_base_url,
                "model": self.model, "source": self.source_lang, "target": self.target_lang,
                "prompts": self.prompts, "glossary": glossary_context, "text": text,
            })
            cached = self._get_cached(keys[text])
            if cached is not None:
                try:
                    validate_translation(text, cached)
                    resolved[text] = cached
                    continue
                except TranslationValidationError:
                    pass
            missing.append(text)
        if missing:
            if self.provider == "deepl":
                results = self._translate_deepl(missing, glossary_context)
            else:
                results = self._translate_llm(missing, glossary_context)
            if len(results) != len(missing):
                raise TranslationValidationError("Response count does not match request")
            for source, translated in zip(missing, results):
                validate_translation(source, translated)
            for source, translated in zip(missing, results):
                # Unchanged dialogue may be a provider failure. Do not make it sticky.
                if source.strip().casefold() != translated.strip().casefold() or self.source_lang == self.target_lang:
                    self._save_cache(keys[source], translated)
                resolved[source] = translated
        return [resolved[text] for text in texts]

    def _check_cancelled(self):
        if self._cancel_event.is_set():
            raise CancelledError("Translation cancelled")

    def _wait(self, seconds: float):
        if self._cancel_event.wait(max(0.0, seconds)):
            raise CancelledError("Translation cancelled")

    @staticmethod
    def _retry_after_seconds(value: str) -> float:
        try:
            seconds = float(value)
        except ValueError:
            try:
                seconds = parsedate_to_datetime(value).timestamp() - time.time()
            except (TypeError, ValueError, OverflowError):
                return 0.0
        return max(0.0, seconds) if math.isfinite(seconds) else 0.0

    def _request_json(self, path: str, payload: dict, max_retries: int | None = None) -> dict:
        if not self.api_key:
            raise ValueError(f"Missing API key for {self.provider}")
        # max_retries retains the existing meaning: total attempts, not retries
        # after the first request. DeepL gets a longer recovery window.
        attempts = max_retries if max_retries is not None else (8 if self.provider == "deepl" else 3)
        if attempts < 1:
            raise ValueError("max_retries must be positive")
        if self.provider != "deepl":
            return self._request_json_attempts(path, payload, attempts)
        while not self._request_lock.acquire(timeout=0.1):
            self._check_cancelled()
        try:
            while True:
                self._check_cancelled()
                if self._deepl_quota_response is not None:
                    raise DeepLQuotaExceededError(self._deepl_quota_response, self.cache_dir, len(self._deepl_keys))
                try:
                    return self._request_json_attempts(path, payload, attempts)
                except DeepLQuotaExceededError as error:
                    # The shared gate makes the key change atomic for all workers.
                    # Retry only the rejected request, preserving the payload and
                    # pacing. Each new key has its own transient-error attempts.
                    if self._deepl_key_index + 1 >= len(self._deepl_keys):
                        self._deepl_quota_response = error.response
                        raise DeepLQuotaExceededError(error.response, self.cache_dir, len(self._deepl_keys)) from error
                    self._deepl_key_index += 1
                    self.api_key = self._deepl_keys[self._deepl_key_index]
                    self.base_url = (self._deepl_custom_base_url or (
                        "https://api-free.deepl.com/v2" if self.api_key.endswith(":fx")
                        else "https://api.deepl.com/v2"
                    )).rstrip("/")
                    if self._on_log:
                        self._on_log("warning", f"DeepL: quota esaurita per la chiave {self._deepl_key_index}; "
                                     f"passaggio alla chiave {self._deepl_key_index + 1}/{len(self._deepl_keys)}.")
        finally:
            self._request_lock.release()

    def _request_json_attempts(self, path: str, payload: dict, attempts: int) -> dict:
        auth = "DeepL-Auth-Key" if self.provider == "deepl" else "Bearer"
        for attempt in range(attempts):
            self._check_cancelled()
            if self.provider == "deepl":
                self._wait(self._next_request_at - time.monotonic())
            delay = min(60.0, 2.0 ** (attempt + 1)) + random.uniform(0.0, 1.0)
            reason = "network error"
            try:
                try:
                    response = self._client.post(
                        f"{self.base_url}/{path}",
                        headers={"Authorization": f"{auth} {self.api_key}"}, json=payload,
                    )
                finally:
                    if self.provider == "deepl":
                        self._next_request_at = time.monotonic() + self._request_interval
                response.raise_for_status()
                if self.provider == "deepl":
                    self._request_interval = max(1.0, self._request_interval * 0.9)
                return response.json()
            except httpx.HTTPStatusError as error:
                # Authentication errors and DeepL quota exhaustion (456) are terminal.
                status = error.response.status_code
                if status == 456 and self.provider == "deepl":
                    raise DeepLQuotaExceededError(error.response, self.cache_dir) from error
                if status not in (429, 500, 502, 503, 504):
                    raise
                reason = f"HTTP {status}"
                # Never shorten a server-requested wait, including HTTP dates.
                delay = max(delay, self._retry_after_seconds(error.response.headers.get("Retry-After", "")))
                if status == 429 and self.provider == "deepl":
                    self._request_interval = min(30.0, self._request_interval * 2)
                    delay = max(delay, self._request_interval)
                if self.provider == "deepl":
                    self._next_request_at = max(self._next_request_at, time.monotonic() + delay)
                if attempt == attempts - 1:
                    raise
            except httpx.TransportError:
                if attempt == attempts - 1:
                    raise
            if self._on_log:
                self._on_log("warning", f"{self.provider}: {reason}; retrying in {delay:.1f}s "
                             f"(attempt {attempt + 2}/{attempts})")
            if self._on_retry_wait:
                self._on_retry_wait(delay)
            if self.provider == "deepl":
                self._next_request_at = max(self._next_request_at, time.monotonic() + delay)
            else:
                self._wait(delay)
        raise RuntimeError("Translation request failed")

    def _call_api(self, system: str, user: str, max_retries: int = 3) -> str:
        data = self._request_json("chat/completions", {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.3, "max_tokens": 4096,
        }, max_retries)
        try:
            choice = data["choices"][0]
            if choice.get("finish_reason") == "length":
                raise TranslationValidationError("Translation truncated by token limit")
            content = choice["message"]["content"]
            if not isinstance(content, str):
                raise ValueError("Non-text response")
            return content.strip()
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise TranslationValidationError("Invalid chat completion response") from error

    def _translate_llm(self, texts: list[str], glossary: str) -> list[str]:
        system = self.prompts["system"].replace("{glossary}", glossary or "(none)")
        user = self.prompts["user"].replace("{texts}", " ||| ".join(texts))
        try:
            content = self._call_api(system, user)
            parts = [part.strip() for part in content.split("|||")]
            if len(parts) != len(texts):
                raise TranslationValidationError("Misaligned batch response")
            for source, translated in zip(texts, parts):
                validate_translation(source, translated)
            return parts
        except TranslationValidationError:
            # Retry malformed batches individually; never accept a partial response.
            results = []
            for source in texts:
                content = self._call_api(system, "Translate this single text. Return only its translation, "
                                         "without delimiters or commentary:\n" + source)
                validate_translation(source, content)
                results.append(content)
            return results

    def _translate_deepl(self, texts: list[str], glossary: str) -> list[str]:
        languages = {"en": "EN", "it": "IT", "de": "DE", "fr": "FR", "es": "ES", "zh-Hans": "ZH"}
        if self.source_lang not in languages or self.target_lang not in languages:
            raise ValueError("Unsupported DeepL language")
        terms = {}
        for line in glossary.splitlines():
            source, separator, target = line.strip().partition(" = ")
            # DeepL needs to see the generic noun to choose Italian articles.
            # Species and other proper names remain protected by the glossary.
            if separator and source and target and source.casefold() not in {"pokémon", "pokemon", "pokèmon"}:
                terms[source.casefold()] = target
        term_pattern = (re.compile(r"(?<!\w)(?:" + "|".join(
            re.escape(term) for term in sorted(terms, key=len, reverse=True)
        ) + r")(?!\w)", re.IGNORECASE) if terms else None)

        def xml_segment(segment):
            if term_pattern is None:
                return escape(segment)
            pieces, offset = [], 0
            for match in term_pattern.finditer(segment):
                pieces.extend((escape(segment[offset:match.start()]),
                               "<keep>" + escape(terms[match.group().casefold()]) + "</keep>"))
                offset = match.end()
            return "".join(pieces) + escape(segment[offset:])

        # Protect placeholders and proper nouns with XML ignore_tags.
        def xml_text(text):
            pieces, offset = [], 0
            for match in TOKEN_RE.finditer(text):
                pieces.extend((xml_segment(text[offset:match.start()]), "<keep>" + match.group() + "</keep>"))
                offset = match.end()
            pieces.append(xml_segment(text[offset:]))
            return "<text>" + "".join(pieces) + "</text>"

        base = {
            "source_lang": languages[self.source_lang], "target_lang": languages[self.target_lang],
            "tag_handling": "xml", "tag_handling_version": "v1", "ignore_tags": ["keep"],
            "preserve_formatting": True,
            "context": "Dialogue and interface text from a Pokemon role-playing video game. " + glossary,
        }
        if self.target_lang == "it":
            base["formality"] = "prefer_less"
        results, batch = [], []

        def send(batch):
            data = self._request_json("translate", {**base, "text": batch})
            try:
                translations = data["translations"]
                if len(translations) != len(batch):
                    raise ValueError("Misaligned DeepL response")
                decoded = []
                for item in translations:
                    root = ET.fromstring(item["text"])
                    if root.tag != "text":
                        raise ValueError("Missing XML root")
                    decoded.append("".join(root.itertext()))
                return decoded
            except (KeyError, TypeError, ValueError, ET.ParseError) as error:
                raise TranslationValidationError("Invalid DeepL response") from error

        def body_size(batch):
            # Conservative reserve under the documented 128 KiB request limit.
            return len(json.dumps({**base, "text": batch}, ensure_ascii=False).encode("utf-8"))

        for text in texts:
            encoded = xml_text(text)
            if body_size([encoded]) > 120 * 1024:
                raise ValueError("Single text exceeds DeepL request size limit")
            if batch and (len(batch) == 50 or body_size(batch + [encoded]) > 120 * 1024):
                results.extend(send(batch))
                batch = []
            batch.append(encoded)
        if batch:
            results.extend(send(batch))
        return results
