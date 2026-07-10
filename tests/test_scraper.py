"""
tests/test_scraper.py - Scraper 模块测试
"""

import pytest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from common import load_config, load_companies, parse_transcript_header, extract_body


@pytest.fixture
def cfg():
    return load_config()


class TestScraperHelpers:
    def test_parse_and_extract_roundtrip(self, cfg):
        """parse_transcript_header + extract_body 可以正确解析实际文件。"""
        from common import get_path, FileNaming
        fn = FileNaming(cfg)
        files = fn.find_english_files()
        if not files:
            pytest.skip("No transcript files found")

        content = files[0].read_text(encoding="utf-8")
        meta = parse_transcript_header(content)
        body = extract_body(content)

        assert "Company" in meta
        assert "Quarter" in meta
        assert len(body) > 0
        assert "=" * 70 not in body  # body 不应包含分隔符

    def test_file_naming_english_files(self, cfg):
        """FileNaming.find_english_files 返回 .txt 文件列表。"""
        from common import FileNaming
        fn = FileNaming(cfg)
        files = fn.find_english_files()
        for f in files:
            assert f.suffix == ".txt"
            assert "_earnings_call" in f.name

    def test_file_naming_bilingual_conversion(self, cfg):
        """FileNaming.english_to_bilingual 正确转换路径。"""
        from common import FileNaming
        fn = FileNaming(cfg)
        english = Path("transcripts/MSFT/MSFT_Q3_2026_earnings_call.txt")
        bilingual = fn.english_to_bilingual(english)
        assert bilingual.name == "MSFT_Q3_2026_bilingual.json"


class TestScraperConfig:
    def test_fool_config_present(self, cfg):
        """fool 配置区块包含必要字段。"""
        fool = cfg.get("fool", {})
        assert "quote_url" in fool
        assert "exchanges" in fool
        assert "request_timeout" in fool

    def test_fmp_config_present(self, cfg):
        """fmp 配置区块包含必要字段。"""
        fmp = cfg.get("fmp", {})
        assert "base_url" in fmp
        assert "endpoint" in fmp
