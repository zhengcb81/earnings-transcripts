"""
tests/test_reader.py - Reader 模块测试
"""

import pytest
from pathlib import Path

import sys
from http.client import HTTPConnection
from http.server import HTTPServer
from threading import Thread
sys.path.insert(0, str(Path(__file__).parent.parent))

from reader import ReaderHandler
from common import load_config


@pytest.fixture
def cfg():
    return load_config()


def test_homepage_serves_real_template_and_company_data(tmp_path, monkeypatch):
    (tmp_path / "companies.txt").write_text("TEST|测试公司|Example Inc|nasdaq\n", encoding="utf-8")
    monkeypatch.setattr("reader.load_config", lambda: {
        "paths": {
            "base_dir": str(tmp_path),
            "companies_file": "companies.txt",
            "transcripts_dir": "transcripts",
        },
    })
    with HTTPServer(("127.0.0.1", 0), ReaderHandler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = HTTPConnection(*server.server_address, timeout=5)
        try:
            for route in ("/", "/index.html"):
                client.request("GET", route)
                response = client.getresponse()
                assert response.status == 200
                assert response.headers["Content-Type"] == "text/html; charset=utf-8"
                assert "测试公司" in response.read().decode("utf-8")
        finally:
            client.close()
            server.shutdown()
            thread.join(5)


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
