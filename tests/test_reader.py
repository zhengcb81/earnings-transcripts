"""
tests/test_reader.py - Reader 模块测试
"""

import json
import pytest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from reader import ReaderHandler
from common import load_config, load_companies


@pytest.fixture
def cfg():
    return load_config()


class TestReaderHandler:
    def test_build_data_returns_expected_keys(self, cfg):
        """build_data 返回 companies 和 transcripts 两个键。"""
        handler = ReaderHandler.__new__(ReaderHandler)
        data = handler.build_data()
        assert "companies" in data
        assert "transcripts" in data

    def test_build_data_companies_not_empty(self, cfg):
        """companies 列表非空。"""
        handler = ReaderHandler.__new__(ReaderHandler)
        data = handler.build_data()
        assert len(data["companies"]) > 0

    def test_build_data_company_has_required_fields(self, cfg):
        """每个 company 包含 ticker, name_cn, name_en 字段。"""
        handler = ReaderHandler.__new__(ReaderHandler)
        data = handler.build_data()
        for c in data["companies"]:
            assert "ticker" in c
            assert "name_cn" in c
            assert "name_en" in c

    def test_build_data_transcripts_dict(self, cfg):
        """transcripts 是字典，key 为 ticker。"""
        handler = ReaderHandler.__new__(ReaderHandler)
        data = handler.build_data()
        assert isinstance(data["transcripts"], dict)
        for ticker in data["transcripts"]:
            assert isinstance(data["transcripts"][ticker], list)

    def test_build_data_transcript_entry_fields(self, cfg):
        """每个 transcript entry 包含必要字段。"""
        handler = ReaderHandler.__new__(ReaderHandler)
        data = handler.build_data()
        for ticker, entries in data["transcripts"].items():
            for entry in entries:
                assert "quarter" in entry
                assert "company" in entry
                assert "content" in entry
                assert "char_count" in entry
                assert "filename" in entry
                assert "bilingual_data" in entry
                assert isinstance(entry["char_count"], int)

    def test_build_data_transcript_content_not_empty(self, cfg):
        """transcript 内容非空。"""
        handler = ReaderHandler.__new__(ReaderHandler)
        data = handler.build_data()
        for ticker, entries in data["transcripts"].items():
            for entry in entries:
                assert len(entry["content"]) > 0

    def test_template_file_exists(self):
        """HTML 模板文件存在。"""
        template = Path(__file__).parent.parent / "templates" / "index.html"
        assert template.exists()
        content = template.read_text(encoding="utf-8")
        assert "__DATA__" in content
        assert "__END__" in content
