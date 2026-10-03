"""
tests/test_scraper.py - Scraper 模块测试
"""

import pytest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from common import load_config, load_companies, parse_transcript_header, extract_body
from scraper import find_english_file, plan_action


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def fn(cfg):
    from common import FileNaming
    return FileNaming(cfg)


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


class TestIncrementalSkip:
    def test_find_english_file_matches_save_transcript(self, cfg):
        """find_english_file 的路径规则必须与 save_transcript 的落盘路径一致，否则跳过判断会失效。"""
        from common import FileNaming
        from scraper import find_english_file
        fn = FileNaming(cfg)

        for out in (None, Path("transcripts")):
            got = find_english_file(fn, "MSFT", "Q3 2026", out)
            assert got.name == "MSFT_Q3_2026_earnings_call.txt"
            if out:
                assert got.parent.name == "MSFT"
                assert got.parent.parent == Path("transcripts")

    def test_completed_entry_reads_meta_from_file(self, cfg):
        """completed_entry 能从已有英文原文里读出 Characters / URL，供 summary 与缓存回填使用。"""
        from common import FileNaming
        from scraper import completed_entry
        fn = FileNaming(cfg)
        files = fn.find_english_files()
        if not files:
            pytest.skip("No transcript files found")

        company = {"ticker": "TEST", "name_en": "TestCo", "name_cn": "测试"}
        entry = completed_entry(cfg, files[0], company, "Q3 2026", "https://example.com/x")

        assert entry["quarter"] == "Q3 2026"
        assert entry["url"] == "https://example.com/x"
        assert isinstance(entry["char_count"], int)
        assert entry["char_count"] > 0          # 真实文件头里带 Characters: N
        assert entry["local_file"] == str(files[0])

    def test_completed_entry_tolerates_missing_file(self, cfg):
        """文件不存在时不应抛异常。"""
        from scraper import completed_entry
        company = {"ticker": "NOPE", "name_en": "Nope", "name_cn": "无"}
        entry = completed_entry(cfg, Path("transcripts/NOPE/NOPE_Q1_2020_earnings_call.txt"),
                                company, "Q1 2020")
        assert entry["char_count"] == 0


class TestPlanAction:
    """plan_action 决定每个季度的去向，--dry-run 与真实跳过判断共用它。"""

    @staticmethod
    def _seed(fn, root, ticker, quarter, with_bilingual):
        p = find_english_file(fn, ticker, quarter, root)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("English", encoding="utf-8")
        if with_bilingual:
            fn.english_to_bilingual(p).write_text("{}", encoding="utf-8")

    def test_missing_english_needs_download(self, fn, tmp_path):
        assert plan_action(fn, "MSFT", "Q1 2020", tmp_path) == "download"

    def test_english_without_translation_needs_translate_when_enabled(self, fn, tmp_path):
        self._seed(fn, tmp_path, "MSFT", "Q1 2020", with_bilingual=False)
        assert plan_action(fn, "MSFT", "Q1 2020", tmp_path, translate_enabled=True) == "translate"

    def test_english_without_translation_is_skipped_by_default(self, fn, tmp_path):
        """默认（无 --translate）不补翻译：原文已在即 skip。"""
        self._seed(fn, tmp_path, "MSFT", "Q1 2020", with_bilingual=False)
        assert plan_action(fn, "MSFT", "Q1 2020", tmp_path) == "skip"

    def test_english_without_translation_is_skipped_when_no_translate(self, fn, tmp_path):
        self._seed(fn, tmp_path, "MSFT", "Q1 2020", with_bilingual=False)
        assert plan_action(fn, "MSFT", "Q1 2020", tmp_path, translate_enabled=False) == "skip"

    def test_complete_pair_is_skipped(self, fn, tmp_path):
        self._seed(fn, tmp_path, "MSFT", "Q1 2020", with_bilingual=True)
        assert plan_action(fn, "MSFT", "Q1 2020", tmp_path, translate_enabled=True) == "skip"

    def test_download_never_reported_for_existing_english(self, fn, tmp_path):
        """核心诉求：英文已存在就绝不应该是 download（那意味着会覆盖）。"""
        for with_bilingual in (True, False):
            self._seed(fn, tmp_path, "FIG", "Q2 2026", with_bilingual)
            assert plan_action(fn, "FIG", "Q2 2026", tmp_path) != "download"


class TestSaveSummary:
    def test_quarter_from_filename_roundtrip(self, cfg):
        """文件名与季度字符串能互相还原，否则按磁盘生成 summary 时会对不上。"""
        from common import FileNaming
        from scraper import find_english_file, quarter_from_filename
        fn = FileNaming(cfg)
        for ticker, quarter in [("MSFT", "Q3 2026"), ("SNOW", "Q1 2027"), ("NVO", "Q4 2023")]:
            path = find_english_file(fn, ticker, quarter, Path("transcripts"))
            assert quarter_from_filename(fn, ticker, path) == quarter

    def test_summary_lists_all_local_files_not_just_this_run(self, cfg, tmp_path):
        """summary 按磁盘文件生成：只跑 1 个季度也不能把既有清单截断。"""
        from common import FileNaming, load_companies
        from scraper import save_summary, quarter_from_filename
        fn = FileNaming(cfg)
        companies = load_companies(cfg, filter_ticker="MSFT")
        files = fn.find_english_files("MSFT")
        if not files:
            pytest.skip("No MSFT transcript files found")

        save_summary(cfg, fn, companies, tmp_path)
        text = (tmp_path / "summary.txt").read_text(encoding="utf-8")

        for f in files:                      # 每个本地文件都必须在清单里
            assert quarter_from_filename(fn, "MSFT", f) in text
        assert f"Total: {len(files)}" in text


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
