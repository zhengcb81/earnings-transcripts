"""
tests/test_make_interleaved.py - make_interleaved 模块测试
"""

import json
import pytest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from common import load_config
from make_interleaved import make_interleaved


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def mock_cfg(tmp_path):
    """Minimal mock config for isolated tests."""
    return {
        "paths": {
            "base_dir": str(tmp_path),
            "transcripts_dir": "transcripts",
        },
        "naming": {
            "english_suffix": "_earnings_call",
            "bilingual_suffix": "_bilingual",
            "interleaved_suffix": "_interleaved",
            "english_ext": ".txt",
            "bilingual_ext": ".json",
            "interleaved_ext": ".txt",
        },
        "format": {
            "separator_width": 70,
            "separator_char": "=",
            "datetime_separator_width": 50,
            "datetime_separator_char": "─",
            "language_labels": {"en": "[EN]", "zh": "[中]"},
        },
        "line_classification": {
            "max_participant_length": 120,
            "max_header_length": 100,
            "header_keywords": ["Revenue", "Q&A"],
        },
    }


@pytest.fixture
def sample_bilingual(tmp_path):
    """Create a sample bilingual JSON file."""
    data = {
        "meta": {
            "company": "Microsoft",
            "quarter": "Q3 2026",
            "source": "motley_fool",
            "url": "https://example.com",
            "translated": "2026-05-29",
            "backend": "mimo",
        },
        "pairs": [
            {"en": "Wednesday, Apr. 29, 2026 at 5:30 p.m. ET", "zh": "2026年4月29日星期三下午5:30 ET"},
            {"en": "Call participants", "zh": "电话会议参与者"},
            {"en": "CEO — Satya Nadella", "zh": "首席执行官 — Satya Nadella"},
            {"en": "Revenue grew 18% in constant currency.", "zh": "按固定汇率计算，营收增长18%。"},
        ],
    }
    d = tmp_path / "MSFT"
    d.mkdir()
    f = d / "MSFT_Q3_2026_bilingual.json"
    f.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return f


class TestMakeInterleaved:
    def test_basic_output(self, mock_cfg, sample_bilingual):
        """生成的 TXT 包含 EN/ZH 标签。"""
        result = make_interleaved(mock_cfg, sample_bilingual)
        assert result is not None
        assert result.exists()
        content = result.read_text(encoding="utf-8")
        assert "[EN]" in content
        assert "[中]" in content

    def test_header_in_output(self, mock_cfg, sample_bilingual):
        """输出包含公司和季度信息。"""
        result = make_interleaved(mock_cfg, sample_bilingual)
        content = result.read_text(encoding="utf-8")
        assert "Microsoft" in content
        assert "Q3 2026" in content

    def test_separator_in_output(self, mock_cfg, sample_bilingual):
        """输出包含分隔符。"""
        result = make_interleaved(mock_cfg, sample_bilingual)
        content = result.read_text(encoding="utf-8")
        assert "=" * 70 in content

    def test_empty_pairs(self, mock_cfg, tmp_path):
        """空 pairs 返回 None。"""
        data = {"meta": {"company": "Test"}, "pairs": []}
        d = tmp_path / "TEST"
        d.mkdir()
        f = d / "TEST_bilingual.json"
        f.write_text(json.dumps(data), encoding="utf-8")
        result = make_interleaved(mock_cfg, f)
        assert result is None

    def test_paragraph_count(self, mock_cfg, sample_bilingual):
        """输出包含段落数统计。"""
        result = make_interleaved(mock_cfg, sample_bilingual)
        content = result.read_text(encoding="utf-8")
        assert "4" in content  # 4 pairs

    def test_interleaved_file_path(self, mock_cfg, sample_bilingual):
        """输出文件名符合 interleaved 命名规则。"""
        result = make_interleaved(mock_cfg, sample_bilingual)
        assert "_interleaved" in result.name
        assert result.suffix == ".txt"
