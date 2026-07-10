"""统一的文件路径/命名规则。"""

from pathlib import Path

from config import get_path


class FileNaming:
    """统一的文件路径/命名规则。"""

    def __init__(self, cfg: dict):
        n = cfg.get("naming", {})
        self.english_suffix = n.get("english_suffix", "_earnings_call")
        self.bilingual_suffix = n.get("bilingual_suffix", "_bilingual")
        self.interleaved_suffix = n.get("interleaved_suffix", "_interleaved")
        self.english_ext = n.get("english_ext", ".txt")
        self.bilingual_ext = n.get("bilingual_ext", ".json")
        self.interleaved_ext = n.get("interleaved_ext", ".txt")
        self.transcripts_dir = get_path(cfg, "transcripts_dir")

    def company_dir(self, ticker: str) -> Path:
        """返回公司目录路径: transcripts_dir/{ticker}/"""
        return self.transcripts_dir / ticker

    def english_path(self, ticker: str, quarter: str) -> Path:
        """返回英文原文文件路径。"""
        q = quarter.replace(" ", "_")
        return self.company_dir(ticker) / f"{ticker}_{q}{self.english_suffix}{self.english_ext}"

    def bilingual_path(self, ticker: str, quarter: str) -> Path:
        """返回中英对照文件路径。"""
        q = quarter.replace(" ", "_")
        return self.company_dir(ticker) / f"{ticker}_{q}{self.bilingual_suffix}{self.bilingual_ext}"

    def interleaved_path(self, ticker: str, quarter: str) -> Path:
        """返回夹排文件路径。"""
        q = quarter.replace(" ", "_")
        return self.company_dir(ticker) / f"{ticker}_{q}{self.interleaved_suffix}{self.interleaved_ext}"

    def english_to_bilingual(self, english_path: Path) -> Path:
        """将英文文件路径转换为对应的 bilingual 文件路径。"""
        return english_path.parent / english_path.name.replace(self.english_suffix, self.bilingual_suffix).replace(self.english_ext, self.bilingual_ext)

    def bilingual_to_interleaved(self, bilingual_path: Path) -> Path:
        """将 bilingual 文件路径转换为对应的 interleaved 文件路径。"""
        return bilingual_path.parent / bilingual_path.name.replace(self.bilingual_suffix, self.interleaved_suffix).replace(self.bilingual_ext, self.interleaved_ext)

    def find_english_files(self, ticker: str = None) -> list[Path]:
        """查找所有英文原文文件，可选按 ticker 过滤。"""
        pattern = f"*{self.english_suffix}{self.english_ext}"
        if ticker:
            d = self.company_dir(ticker)
            return sorted(d.glob(pattern)) if d.exists() else []
        files = []
        for d in sorted(self.transcripts_dir.iterdir()):
            if d.is_dir():
                files.extend(d.glob(pattern))
        return sorted(files)

    def find_bilingual_files(self, ticker: str = None) -> list[Path]:
        """查找所有 bilingual 文件，可选按 ticker 过滤。"""
        pattern = f"*{self.bilingual_suffix}{self.bilingual_ext}"
        if ticker:
            d = self.company_dir(ticker)
            return sorted(d.glob(pattern)) if d.exists() else []
        files = []
        for d in sorted(self.transcripts_dir.iterdir()):
            if d.is_dir():
                files.extend(d.glob(pattern))
        return sorted(files)
