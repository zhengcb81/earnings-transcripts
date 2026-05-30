"""
tests/test_common.py - 共享模块单元测试
"""

import json
import tempfile
import pytest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from common import (
    load_config, get_path, load_companies, FileNaming, JsonCache,
    LineClassifier, split_paragraphs, text_hash, parse_transcript_header,
    extract_body, DeepSeekTranslator, GoogleTranslator,
)


# ── Fixtures ──

@pytest.fixture
def cfg():
    """Load real config.yaml."""
    return load_config()


@pytest.fixture
def tmp_dir(tmp_path):
    """Create a temp directory with test data."""
    return tmp_path


@pytest.fixture
def sample_companies(tmp_dir):
    """Create a sample companies.txt."""
    f = tmp_dir / "companies.txt"
    f.write_text(
        "# comment\n"
        "MSFT|微软|Microsoft|nasdaq\n"
        "SNOW|Snowflake|Snowflake|nyse\n"
        "# another comment\n"
        "FIG|Figma|Figma|nyse\n",
        encoding="utf-8",
    )
    return f


@pytest.fixture
def sample_transcript(tmp_dir):
    """Create a sample transcript file."""
    sep = "=" * 70
    content = f"""{sep}
Earnings Call Transcript
Company: Microsoft (微软)
Ticker: MSFT
Quarter: Q3 2026
Source: motley_fool
URL: https://example.com
Scraped: 2026-05-29T22:26:47
Characters: 1234
{sep}

Wednesday, Apr. 29, 2026 at 5:30 p.m. ET

Call participants

Chairman and Chief Executive Officer — Satya Nadella

Chief Financial Officer — Amy Hood

Revenue-- $82.9 billion, up 18% in constant currency.

Operating Income-- Increased 16% in constant currency.
"""
    d = tmp_dir / "MSFT"
    d.mkdir()
    f = d / "MSFT_Q3_2026_earnings_call.txt"
    f.write_text(content, encoding="utf-8")
    return f


@pytest.fixture
def sample_bilingual(tmp_dir):
    """Create a sample bilingual JSON."""
    data = {
        "meta": {
            "company": "Microsoft (微软)",
            "quarter": "Q3 2026",
            "source": "motley_fool",
            "url": "https://example.com",
            "translated": "2026-05-29T22:26:47",
            "backend": "deepseek",
        },
        "pairs": [
            {"en": "Wednesday, Apr. 29, 2026 at 5:30 p.m. ET", "zh": "2026年4月29日星期三下午5:30 ET"},
            {"en": "Call participants", "zh": "电话会议参与者"},
            {"en": "CEO — Satya Nadella", "zh": "首席执行官 — Satya Nadella"},
            {"en": "Revenue grew 18% in constant currency.", "zh": "按固定汇率计算，营收增长18%。"},
        ],
    }
    d = tmp_dir / "MSFT"
    d.mkdir(exist_ok=True)
    f = d / "MSFT_Q3_2026_bilingual.json"
    f.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return f


# ── Config Tests ──

class TestConfig:
    def test_load_config(self, cfg):
        assert "paths" in cfg
        assert "fool" in cfg
        assert "deepseek" in cfg
        assert "reader" in cfg

    def test_config_has_all_sections(self, cfg):
        expected = ["paths", "naming", "fool", "fmp", "deepseek", "google",
                     "translate", "format", "line_classification", "reader",
                     "logging", "companies"]
        for key in expected:
            assert key in cfg, f"Missing config section: {key}"

    def test_get_path(self, cfg):
        p = get_path(cfg, "transcripts_dir")
        assert isinstance(p, Path)
        assert p.name == "transcripts"


# ── Company Tests ──

class TestCompanies:
    def test_load_companies(self, cfg, sample_companies):
        cfg["paths"]["base_dir"] = str(sample_companies.parent)
        cfg["paths"]["companies_file"] = sample_companies.name
        companies = load_companies(cfg)
        assert len(companies) == 3
        assert companies[0]["ticker"] == "MSFT"
        assert companies[1]["ticker"] == "SNOW"
        assert companies[2]["ticker"] == "FIG"

    def test_filter_ticker(self, cfg, sample_companies):
        cfg["paths"]["base_dir"] = str(sample_companies.parent)
        cfg["paths"]["companies_file"] = sample_companies.name
        companies = load_companies(cfg, filter_ticker="MSFT")
        assert len(companies) == 1
        assert companies[0]["ticker"] == "MSFT"

    def test_comments_skipped(self, cfg, sample_companies):
        cfg["paths"]["base_dir"] = str(sample_companies.parent)
        cfg["paths"]["companies_file"] = sample_companies.name
        companies = load_companies(cfg)
        tickers = [c["ticker"] for c in companies]
        assert "#" not in tickers


# ── FileNaming Tests ──

class TestFileNaming:
    def test_english_path(self, cfg):
        fn = FileNaming(cfg)
        p = fn.english_path("MSFT", "Q3 2026")
        assert p.name == "MSFT_Q3_2026_earnings_call.txt"

    def test_bilingual_path(self, cfg):
        fn = FileNaming(cfg)
        p = fn.bilingual_path("MSFT", "Q3 2026")
        assert p.name == "MSFT_Q3_2026_bilingual.json"

    def test_interleaved_path(self, cfg):
        fn = FileNaming(cfg)
        p = fn.interleaved_path("MSFT", "Q3 2026")
        assert p.name == "MSFT_Q3_2026_interleaved.txt"

    def test_english_to_bilingual(self, cfg):
        fn = FileNaming(cfg)
        eng = fn.english_path("MSFT", "Q3 2026")
        bi = fn.english_to_bilingual(eng)
        assert bi.name == "MSFT_Q3_2026_bilingual.json"

    def test_bilingual_to_interleaved(self, cfg):
        fn = FileNaming(cfg)
        bi = fn.bilingual_path("MSFT", "Q3 2026")
        il = fn.bilingual_to_interleaved(bi)
        assert il.name == "MSFT_Q3_2026_interleaved.txt"


# ── JsonCache Tests ──

class TestJsonCache:
    def test_create_empty(self, tmp_dir):
        c = JsonCache(tmp_dir / "cache.json")
        assert len(c) == 0

    def test_set_get(self, tmp_dir):
        c = JsonCache(tmp_dir / "cache.json")
        c.set("key1", "value1")
        assert c.get("key1") == "value1"
        assert "key1" in c

    def test_save_load(self, tmp_dir):
        path = tmp_dir / "cache.json"
        c = JsonCache(path)
        c.set("k", "v")
        c.save()

        c2 = JsonCache(path)
        assert c2.get("k") == "v"

    def test_corrupt_file(self, tmp_dir):
        path = tmp_dir / "cache.json"
        path.write_text("not json!", encoding="utf-8")
        c = JsonCache(path)
        assert len(c) == 0  # should not crash


# ── LineClassifier Tests ──

class TestLineClassifier:
    def test_datetime(self, cfg):
        lc = LineClassifier(cfg)
        assert lc.classify("Wednesday, Apr. 29, 2026 at 5:30 p.m. ET") == "datetime"

    def test_time(self, cfg):
        lc = LineClassifier(cfg)
        assert lc.classify("5:30 p.m. ET") == "datetime"

    def test_participant(self, cfg):
        lc = LineClassifier(cfg)
        assert lc.classify("Chairman and CEO — Satya Nadella") == "participant"

    def test_header(self, cfg):
        lc = LineClassifier(cfg)
        assert lc.classify("Revenue-- $82.9 billion") == "header"

    def test_body(self, cfg):
        lc = LineClassifier(cfg)
        assert lc.classify("This is a regular paragraph with enough text to not be filtered.") == "body"


# ── Paragraph Tests ──

class TestParagraphs:
    def test_split(self):
        text = "para one\n\npara two\n\npara three"
        result = split_paragraphs(text, min_length=1)
        assert len(result) == 3

    def test_min_length(self):
        text = "long paragraph here\n\nab\n\nanother long one"
        result = split_paragraphs(text, min_length=5)
        assert len(result) == 2

    def test_empty(self):
        assert split_paragraphs("") == []


# ── Text Hash Tests ──

class TestTextHash:
    def test_consistent(self):
        assert text_hash("hello") == text_hash("hello")

    def test_different(self):
        assert text_hash("hello") != text_hash("world")

    def test_length(self):
        h = text_hash("test", length=8)
        assert len(h) == 8


# ── Transcript Parsing Tests ──

class TestTranscriptParsing:
    def test_parse_header(self, sample_transcript):
        content = sample_transcript.read_text(encoding="utf-8")
        meta = parse_transcript_header(content)
        assert meta["Company"] == "Microsoft (微软)"
        assert meta["Quarter"] == "Q3 2026"
        assert meta["Ticker"] == "MSFT"

    def test_extract_body(self, sample_transcript):
        content = sample_transcript.read_text(encoding="utf-8")
        body = extract_body(content)
        assert "Satya Nadella" in body
        assert "Revenue" in body
        assert "Earnings Call Transcript" not in body  # header excluded

    def test_parse_invalid(self):
        assert parse_transcript_header("no separator here") == {}


# ── Translator Tests ──

class TestTranslators:
    def test_deepseek_config(self, cfg):
        ds = DeepSeekTranslator(cfg)
        assert ds.model == "deepseek-v4-flash"
        assert ds.name == "deepseek"

    def test_google_config(self, cfg):
        g = GoogleTranslator(cfg)
        assert g.name == "google"

    def test_google_translate(self, cfg):
        g = GoogleTranslator(cfg)
        if g.available():
            result = g.translate("Revenue grew 18%")
            assert len(result) > 0
            assert result != "Revenue grew 18%"
