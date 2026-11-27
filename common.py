"""
common.py - 共享模块（re-export 门面）
所有公共 API 从子模块导入，保持向后兼容。
"""

# 配置加载
from config import (
    get_path as get_path,
    load_companies as load_companies,
    load_config as load_config,
    setup_logging as setup_logging,
)

# 文件命名
from naming import FileNaming as FileNaming

# 缓存
from cache import JsonCache as JsonCache

# 单实例锁（防并发运行互相覆盖）
from lock import SingleInstanceLock as SingleInstanceLock

# 解析
from parser import (
    LineClassifier as LineClassifier,
    build_bilingual_data as build_bilingual_data,
    extract_body as extract_body,
    parse_transcript_header as parse_transcript_header,
    split_paragraphs as split_paragraphs,
)

# 翻译
from translator import (
    DeepSeekTranslator as DeepSeekTranslator,
    GoogleTranslator as GoogleTranslator,
    MiMoTranslator as MiMoTranslator,
    MiniMaxTranslator as MiniMaxTranslator,
    OpenAITranslator,
    TranslatorFactory as TranslatorFactory,
    text_hash as text_hash,
    translate_paragraphs as translate_paragraphs,
)

_OpenAITranslator = OpenAITranslator
