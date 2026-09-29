"""Translation controls used by the legacy scraper and transcript companion."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import scraper  # noqa: E402


@pytest.mark.parametrize("flag", ["--no-translate", "--disable-translation"])
def test_translation_disable_flags_select_no_translate_mode(flag: str) -> None:
    args = scraper.build_argument_parser().parse_args([flag])
    assert args.no_translate is True


def test_skip_mode_returns_before_creating_translator_or_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fail_if_called(*args, **kwargs):
        pytest.fail("translator must not be initialized when translation is disabled")

    monkeypatch.setattr(scraper.TranslatorFactory, "create", fail_if_called)
    english_path = tmp_path / "not-created.txt"
    result = scraper.translate_after_download(
        cfg={}, fn=None, english_path=english_path, skip=True
    )

    assert result is None
    assert list(tmp_path.iterdir()) == []

