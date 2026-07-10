"""翻译体系：OpenAI 兼容 API 基类、子类、工厂、Google 兜底。"""

import json
import re
import hashlib
import logging
import time

from config import get_path
from cache import JsonCache


class _OpenAITranslator:
    """OpenAI 兼容 API 翻译基类（MiMo / MiniMax / DeepSeek 共用）。"""

    # 子类需覆盖
    _backend_name: str = ""
    _config_key: str = ""
    _json_key_prefix: str = ""
    _default_base_url: str = ""
    _default_model: str = ""

    def __init__(self, cfg: dict):
        section = cfg.get(self._config_key, {})
        # 兼容 config.json 格式
        json_cfg = {}
        json_path = get_path(cfg, "config_json")
        if json_path.exists():
            try:
                json_cfg = json.loads(json_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

        prefix = self._json_key_prefix
        self.api_key = json_cfg.get(f"{prefix}_api_key", "")
        self.base_url = json_cfg.get(f"{prefix}_base_url", section.get("base_url", self._default_base_url))
        self.model = json_cfg.get(f"{prefix}_model", section.get("model", self._default_model))
        self.max_tokens = section.get("max_tokens", 4000)
        self.temperature = section.get("temperature", 0.1)
        self.system_prompt = section.get("system_prompt", "") or cfg.get("translate", {}).get("system_prompt", "你是专业金融翻译。翻译为中文。")
        self.user_prompt_tpl = section.get("user_prompt_template", "翻译为中文：\n\n{text}")
        self.availability_prompt = section.get("availability_prompt", "Say OK")
        self.test_tokens = section.get("availability_test_tokens", 5)
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        return self._client

    def available(self) -> bool:
        if not self.api_key:
            return False
        try:
            r = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": self.availability_prompt}],
                max_tokens=self.test_tokens,
            )
            return r.choices is not None and len(r.choices) > 0
        except Exception:
            return False

    def _postprocess(self, text: str) -> str:
        """子类可覆盖，对翻译结果做后处理。"""
        return text

    def translate(self, text: str) -> str:
        r = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": self.user_prompt_tpl.format(text=text)},
            ],
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )
        return self._postprocess(r.choices[0].message.content.strip())

    @property
    def name(self) -> str:
        return self._backend_name


class MiMoTranslator(_OpenAITranslator):
    """小米 MiMo LLM 翻译。"""
    _backend_name = "mimo"
    _config_key = "mimo"
    _json_key_prefix = "mimo"
    _default_base_url = "https://token-plan-cn.xiaomimimo.com/v1"
    _default_model = "mimo-v2.5-pro"


class MiniMaxTranslator(_OpenAITranslator):
    """MiniMax LLM 翻译。"""
    _backend_name = "minimax"
    _config_key = "minimax"
    _json_key_prefix = "minimax"
    _default_base_url = "https://api.minimaxi.com/v1"
    _default_model = "MiniMax-M3"

    def _postprocess(self, text: str) -> str:
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        return text


class DeepSeekTranslator(_OpenAITranslator):
    """DeepSeek LLM 翻译。"""
    _backend_name = "deepseek"
    _config_key = "deepseek"
    _json_key_prefix = "deepseek"
    _default_base_url = "https://api.deepseek.com"
    _default_model = "deepseek-v4-flash"


class TranslatorFactory:
    """翻译后端工厂。"""

    _BACKEND_PRIORITY = ["minimax", "mimo", "deepseek"]
    _BACKEND_CLASSES = {
        "mimo": MiMoTranslator,
        "minimax": MiniMaxTranslator,
        "deepseek": DeepSeekTranslator,
    }

    @staticmethod
    def create(cfg: dict, backend: str = "auto"):
        """
        创建翻译后端。
        backend: "mimo" / "minimax" / "deepseek" / "google" / "auto"
        auto 模式优先级: minimax → mimo → deepseek → google
        """
        if backend == "auto":
            for name in TranslatorFactory._BACKEND_PRIORITY:
                cls = TranslatorFactory._BACKEND_CLASSES[name]
                translator = cls(cfg)
                if translator.available():
                    return translator
            logging.warning("All LLM backends unavailable, falling back to Google")
            return GoogleTranslator(cfg)

        if backend in TranslatorFactory._BACKEND_CLASSES:
            translator = TranslatorFactory._BACKEND_CLASSES[backend](cfg)
            if translator.available():
                return translator
            logging.warning(f"{backend} unavailable, falling back to Google")
            return GoogleTranslator(cfg)

        if backend == "google":
            return GoogleTranslator(cfg)

        raise ValueError(f"Unknown backend: {backend}")


class GoogleTranslator:
    """Google Translate 翻译。"""

    def __init__(self, cfg: dict):
        from translatepy import Translator
        g = cfg.get("google", {})
        self.translator = Translator()
        self.target = g.get("target_language", "Chinese")
        self.test_text = g.get("availability_test_text", "test")

    def available(self) -> bool:
        try:
            r = self.translator.translate(self.test_text, self.target)
            return bool(r.result)
        except Exception:
            return False

    def translate(self, text: str) -> str:
        r = self.translator.translate(text, self.target)
        return r.result

    @property
    def name(self) -> str:
        return "google"


def text_hash(text: str, length: int = 16) -> str:
    """MD5 哈希，截取前 N 位。"""
    return hashlib.md5(text.strip().encode()).hexdigest()[:length]


def translate_paragraphs(
    paragraphs: list[str],
    cache: JsonCache,
    translator,
    cfg: dict,
    logger: logging.Logger = None,
) -> list[str]:
    """
    翻译段落列表，带缓存。
    返回翻译后的列表（与输入等长）。
    """
    t_cfg = cfg.get("translate", {})
    hash_len = t_cfg.get("hash_length", 16)
    min_len = t_cfg.get("min_paragraph_length", 5)
    backend_cfg = cfg.get(translator.name, cfg.get("deepseek", {}))
    g_cfg = cfg.get("google", {})
    save_interval = backend_cfg.get("cache_save_interval", 10)
    sleep_llm = backend_cfg.get("sleep_between_calls", 0.3)
    sleep_g = g_cfg.get("sleep_between_calls", 0.5)
    sleep = sleep_g if translator.name == "google" else sleep_llm

    log = logger or logging.getLogger()
    results = []
    total = len(paragraphs)
    errors = []

    for i, p in enumerate(paragraphs):
        h = text_hash(p, hash_len)
        if h in cache:
            results.append(cache[h])
            continue

        if len(p.strip()) < min_len:
            results.append(p)
            continue

        try:
            result = translator.translate(p)
            cache.set(h, result)
            results.append(result)
        except Exception as e:
            log.warning(f"  Translate error [{i}]: {e}")
            results.append(p)
            errors.append(i)

        if (i + 1) % save_interval == 0:
            log.info(f"  Translation progress: {i+1}/{total}")
            cache.save()

        time.sleep(sleep)

    cache.save()
    if errors:
        log.warning(f"  {len(errors)}/{total} paragraphs failed (using original text): indices {errors[:10]}{'...' if len(errors) > 10 else ''}")
    return results
