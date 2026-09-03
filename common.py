"""
common.py - 共享模块（re-export 门面）
所有公共 API 从子模块导入，保持向后兼容。
"""

# 配置加载
from config import load_config, get_path, setup_logging, load_companies

# 文件命名
from naming import FileNaming

# 缓存
from cache import JsonCache

# 单实例锁（防并发运行互相覆盖）
from lock import SingleInstanceLock

# 解析
from parser import (
    LineClassifier, split_paragraphs,
    parse_transcript_header, extract_body, build_bilingual_data,
)

# 翻译
from translator import (
    _OpenAITranslator, MiMoTranslator, MiniMaxTranslator, DeepSeekTranslator,
    TranslatorFactory, GoogleTranslator,
    text_hash, translate_paragraphs,
)
