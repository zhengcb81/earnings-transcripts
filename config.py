"""配置加载与路径解析。"""

import logging
from pathlib import Path


def load_config(config_path: Path = None) -> dict:
    """加载 config.yaml，返回嵌套 dict。"""
    import yaml  # 延迟导入，仅此处使用
    if config_path is None:
        config_path = Path(__file__).parent / "config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_path(cfg: dict, key: str) -> Path:
    """从 config.paths 获取绝对路径。"""
    base = Path(cfg["paths"]["base_dir"]).resolve()
    return base / cfg["paths"][key]


def setup_logging(cfg: dict, log_file: str = None) -> logging.Logger:
    """根据配置初始化日志。"""
    log_cfg = cfg.get("logging", {})
    fmt = log_cfg.get("format", "%(asctime)s [%(levelname)s] %(message)s")
    level = getattr(logging, log_cfg.get("level", "INFO").upper(), logging.INFO)

    handlers = [logging.StreamHandler()]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(level=level, format=fmt, handlers=handlers)
    return logging.getLogger()


def load_companies(cfg: dict, filter_ticker: str = None) -> list[dict]:
    """从 companies.txt 加载公司列表。"""
    comp_cfg = cfg.get("companies", {})
    delimiter = comp_cfg.get("delimiter", "|")
    comment = comp_cfg.get("comment_prefix", "#")
    fields = comp_cfg.get("fields", ["ticker", "name_cn", "name_en", "exchange"])

    path = get_path(cfg, "companies_file")
    if not path.exists():
        return []

    companies = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith(comment):
            continue
        parts = [p.strip() for p in line.split(delimiter)]
        if len(parts) >= 3:
            entry = {fields[i]: parts[i] for i in range(min(len(parts), len(fields)))}
            if len(parts) < 4:
                entry.setdefault("exchange", "auto")
            companies.append(entry)

    if filter_ticker:
        companies = [c for c in companies if c["ticker"].upper() == filter_ticker.upper()]
    return companies
