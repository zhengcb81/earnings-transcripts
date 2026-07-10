"""段落解析、文件头解析、行分类。"""

import re
from datetime import datetime


class LineClassifier:
    """根据行内容分类（datetime / participant / header / body）。"""

    def __init__(self, cfg: dict):
        lc = cfg.get("line_classification", {})
        self.max_participant = lc.get("max_participant_length", 120)
        self.max_header = lc.get("max_header_length", 100)
        keywords = lc.get("header_keywords", [])
        self.header_pattern = re.compile(
            r"^(" + "|".join(re.escape(k) for k in keywords) + r")",
            re.IGNORECASE
        ) if keywords else None

    def classify(self, line: str) -> str:
        s = line.strip()
        if re.match(r"^\w+,\s+\w+\.\s+\d+", s) or re.match(r"^\d{1,2}:\d{2}", s):
            return "datetime"
        if "—" in s and len(s) < self.max_participant:
            return "participant"
        if self.header_pattern and len(s) < self.max_header and self.header_pattern.search(s):
            return "header"
        return "body"


def split_paragraphs(text: str, min_length: int = 5) -> list[str]:
    """将文本按空行分割为段落列表。"""
    paragraphs = []
    current = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            if current:
                paragraphs.append("\n".join(current))
                current = []
        else:
            current.append(stripped)
    if current:
        paragraphs.append("\n".join(current))
    return [p for p in paragraphs if len(p.strip()) >= min_length]


def parse_transcript_header(content: str, sep: str = None, cfg: dict = None) -> dict:
    """从 transcript 文件内容解析头部元数据。"""
    if sep is None:
        fmt = (cfg or {}).get("format", {})
        sep = fmt.get("separator_char", "=") * fmt.get("separator_width", 70)
    parts = content.split(sep)
    if len(parts) < 3:
        return {}
    header = parts[1]
    meta = {}
    for line in header.strip().split("\n"):
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta


def extract_body(content: str, sep: str = None, cfg: dict = None) -> str:
    """从 transcript 文件内容提取正文（头部之后的部分）。"""
    if sep is None:
        fmt = (cfg or {}).get("format", {})
        sep = fmt.get("separator_char", "=") * fmt.get("separator_width", 70)
    parts = content.split(sep)
    if len(parts) < 3:
        return content
    return sep.join(parts[2:])


def build_bilingual_data(header_meta: dict, paragraphs: list, translated: list, backend_name: str) -> dict:
    """构建 bilingual JSON 数据结构。"""
    pairs = [{"en": orig, "zh": trans} for orig, trans in zip(paragraphs, translated)]
    return {
        "meta": {
            "company": header_meta.get("Company", ""),
            "quarter": header_meta.get("Quarter", ""),
            "source": header_meta.get("Source", ""),
            "url": header_meta.get("URL", ""),
            "translated": datetime.now().isoformat(),
            "backend": backend_name,
        },
        "pairs": pairs,
    }
