#!/usr/bin/env python3
"""
翻译质量评测：MiMo vs MiniMax vs DeepSeek
从已有 transcript 中采样段落，用三个模型翻译，多维度打分对比。

Usage:
  python3 eval_translation.py                    # 完整评测（9段 x 3模型）
  python3 eval_translation.py --samples 3        # 快速评测（3段 x 3模型）
  python3 eval_translation.py --judge mimo       # 指定裁判模型
"""

import json
import re
import time
import argparse
from pathlib import Path
from datetime import datetime

from common import (
    load_config, get_path, extract_body, split_paragraphs,
    MiMoTranslator, MiniMaxTranslator, DeepSeekTranslator,
    TranslatorFactory,
)


# ── 评测样本选取 ──

def collect_samples(transcripts_dir: Path, n: int = 9) -> list[dict]:
    """从已有 transcript 中选取多样化的评测段落。"""
    candidates = []
    for company_dir in sorted(transcripts_dir.iterdir()):
        if not company_dir.is_dir():
            continue
        for f in sorted(company_dir.glob("*_earnings_call.txt")):
            content = f.read_text(encoding="utf-8")
            body = extract_body(content)
            paras = split_paragraphs(body, min_length=60)
            for p in paras:
                p_clean = p.strip()
                if len(p_clean) > 600:
                    p_clean = p_clean[:600] + "..."
                candidates.append({
                    "company": company_dir.name,
                    "file": f.name,
                    "text": p_clean,
                })

    # 分层采样：确保覆盖不同类型的段落
    samples = []
    # 1) 含数字/百分比的段落（财务指标）
    numeric = [c for c in candidates if re.search(r"\$[\d,.]+|\d+%|\d+\.\d+%", c["text"])]
    # 2) 含人名/职位的段落
    participants = [c for c in candidates if "—" in c["text"] and len(c["text"]) < 200]
    # 3) 一般叙述段落
    narrative = [c for c in candidates if c not in numeric and c not in participants and len(c["text"]) > 100]

    import random
    random.seed(42)
    per_category = max(1, n // 3)
    samples.extend(random.sample(numeric, min(per_category, len(numeric))))
    samples.extend(random.sample(participants, min(per_category, len(participants))))
    samples.extend(random.sample(narrative, min(per_category, len(narrative))))

    # 补足到 n 个
    remaining = [c for c in candidates if c not in samples]
    while len(samples) < n and remaining:
        pick = random.choice(remaining)
        samples.append(pick)
        remaining.remove(pick)

    return samples[:n]


# ── 自动评分维度 ──

def score_number_preservation(en: str, zh: str) -> float:
    """数字/百分比保留度：英文中的数字在中文中是否都保留。"""
    # 提取英文中的关键数字
    en_numbers = set(re.findall(r"\$[\d,.]+[BbMmKk]?\b|\d+(?:\.\d+)?%|\b\d{4}\b|\bQ\d\b", en))
    if not en_numbers:
        return 1.0  # 无数字段，满分
    found = sum(1 for n in en_numbers if n in zh)
    return found / len(en_numbers)


def score_ticker_preservation(en: str, zh: str) -> float:
    """Ticker/品牌名保留度。"""
    tickers = set(re.findall(r"\b[A-Z]{2,5}\b", en))
    # 排除常见全大写非 ticker 词
    stopwords = {"CEO", "CFO", "COO", "CTO", "CMO", "CIO", "IPO", "RPO", "ARR",
                 "EPS", "GAAP", "R&D", "SG&A", "CAPEX", "EBITDA", "USD", "SEC",
                 "Q1", "Q2", "Q3", "Q4", "FY", "YTD", "QoQ", "YoY", "AI", "ML",
                 "US", "UK", "ET", "AM", "PM", "IR", "PR", "OK", "VS", "IT"}
    tickers -= stopwords
    if not tickers:
        return 1.0
    found = sum(1 for t in tickers if t in zh)
    return found / len(tickers)


def score_length_ratio(en: str, zh: str) -> float:
    """长度比：中文翻译不应过短或过长。"""
    ratio = len(zh) / max(len(en), 1)
    if 0.5 <= ratio <= 2.0:
        return 1.0
    elif 0.3 <= ratio < 0.5 or 2.0 < ratio <= 3.0:
        return 0.6
    else:
        return 0.2


def score_no_hallucination(en: str, zh: str) -> float:
    """无幻觉：中文中不应出现英文没有的数字。"""
    zh_numbers = set(re.findall(r"\$[\d,.]+[BbMmKk]?\b|\d+(?:\.\d+)?%", zh))
    en_numbers = set(re.findall(r"\$[\d,.]+[BbMmKk]?\b|\d+(?:\.\d+)?%", en))
    if not zh_numbers:
        return 1.0
    hallucinated = zh_numbers - en_numbers
    return max(0, 1.0 - len(hallucinated) * 0.3)


def auto_score(en: str, zh: str) -> dict:
    """自动评分（无需 LLM）。"""
    return {
        "number_preservation": score_number_preservation(en, zh),
        "ticker_preservation": score_ticker_preservation(en, zh),
        "length_ratio": score_length_ratio(en, zh),
        "no_hallucination": score_no_hallucination(en, zh),
    }


# ── LLM 裁评判分 ──

JUDGE_PROMPT = """你是翻译质量评审专家。请对以下英译中翻译打分。

## 英文原文
{en}

## 中文翻译
{zh}

## 评分维度（每项 1-5 分）

1. **准确性** (accuracy): 翻译是否忠实于原文含义，无遗漏、无添加
2. **术语** (terminology): 金融/科技术语翻译是否专业准确
3. **数字** (numbers): 金额、百分比、ticker 是否原样保留
4. **流畅度** (fluency): 中文是否通顺自然，符合中文表达习惯
5. **格式** (formatting): 人名、职位、标点格式是否规范

请严格按以下 JSON 格式输出，不要输出其他内容：
{{"accuracy": N, "terminology": N, "numbers": N, "fluency": N, "formatting": N, "comment": "一句话点评"}}
"""


def llm_judge(judge_translator, en: str, zh: str) -> dict:
    """用 LLM 裁判打分。"""
    prompt = JUDGE_PROMPT.format(en=en, zh=zh)
    try:
        result = judge_translator.translate(prompt)
        # 尝试从回复中提取 JSON
        match = re.search(r'\{[^{}]+\}', result)
        if match:
            return json.loads(match.group())
    except Exception:
        pass
    return {"accuracy": 0, "terminology": 0, "numbers": 0, "fluency": 0, "formatting": 0, "comment": "评分失败"}


# ── 主评测流程 ──

def run_evaluation(cfg: dict, n_samples: int = 9, judge_name: str = "mimo"):
    transcripts_dir = Path(__file__).parent / "transcripts"
    print(f"{'='*70}")
    print(f"翻译质量评测 - MiMo vs MiniMax vs DeepSeek")
    print(f"{'='*70}")
    print(f"样本数: {n_samples}, 裁判: {judge_name}")
    print()

    # 选取样本
    samples = collect_samples(transcripts_dir, n_samples)
    print(f"已选取 {len(samples)} 个评测段落:")
    for i, s in enumerate(samples):
        print(f"  [{i+1}] {s['company']:6s} ({len(s['text']):3d}字) {s['text'][:60]}...")
    print()

    # 初始化翻译器
    translators = {
        "mimo": MiMoTranslator(cfg),
        "minimax": MiniMaxTranslator(cfg),
        "deepseek": DeepSeekTranslator(cfg),
    }
    judge = translators.get(judge_name) or translators["mimo"]

    # 检查可用性
    for name, t in list(translators.items()):
        if not t.available():
            print(f"[WARNING] {name} unavailable, removing from evaluation")
            del translators[name]

    if len(translators) < 2:
        print("[ERROR] Need at least 2 backends for comparison")
        return

    # 翻译 + 评分
    results = []
    for i, sample in enumerate(samples):
        print(f"\n{'─'*60}")
        print(f"样本 [{i+1}/{len(samples)}] {sample['company']} - {sample['text'][:80]}...")
        print(f"{'─'*60}")

        row = {"sample": sample, "translations": {}}

        for name, translator in translators.items():
            print(f"  翻译中 ({name})...", end="", flush=True)
            start = time.time()
            try:
                zh = translator.translate(sample["text"])
                elapsed = time.time() - start
                print(f" ({elapsed:.1f}s)")

                # 自动评分
                auto = auto_score(sample["text"], zh)

                # LLM 裁判评分（用其他模型评判，避免自己评自己）
                judge_scores = {}
                for jname, jtrans in translators.items():
                    if jname != name:  # 不自己评自己
                        judge_scores[jname] = llm_judge(jtrans, sample["text"], zh)
                        time.sleep(0.5)

                row["translations"][name] = {
                    "zh": zh,
                    "time": elapsed,
                    "auto_score": auto,
                    "judge_scores": judge_scores,
                }

                print(f"    数字保留: {auto['number_preservation']:.0%}")
                print(f"    译文: {zh[:100]}...")

            except Exception as e:
                print(f" ERROR: {e}")
                row["translations"][name] = {"error": str(e)}

        results.append(row)

    # 生成报告
    report = generate_report(results, translators)
    print(report)

    # 保存详细结果
    output_path = Path(__file__).parent / "eval_results.json"
    save_data = []
    for r in results:
        row = {"sample": r["sample"], "translations": {}}
        for name, t in r["translations"].items():
            row["translations"][name] = {k: v for k, v in t.items() if k != "zh"}
            row["translations"][name]["zh_preview"] = t.get("zh", "")[:200]
        save_data.append(row)
    output_path.write_text(json.dumps(save_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n详细结果已保存: {output_path}")


def generate_report(results: list, translators: dict) -> str:
    """生成评测报告。"""
    lines = []
    lines.append(f"\n{'='*70}")
    lines.append("评测报告")
    lines.append(f"{'='*70}")

    backend_names = list(translators.keys())

    # 汇总自动评分
    lines.append(f"\n{'─'*60}")
    lines.append("自动评分汇总 (满分 1.0)")
    lines.append(f"{'─'*60}")

    auto_dims = ["number_preservation", "ticker_preservation", "length_ratio", "no_hallucination"]
    dim_labels = {"number_preservation": "数字保留", "ticker_preservation": "Ticker保留",
                  "length_ratio": "长度合理", "no_hallucination": "无幻觉"}

    header = f"{'维度':<14}" + "".join(f"{n:>12}" for n in backend_names)
    lines.append(header)
    lines.append("─" * len(header))

    for dim in auto_dims:
        scores = {}
        for name in backend_names:
            vals = []
            for r in results:
                t = r["translations"].get(name, {})
                if "auto_score" in t:
                    vals.append(t["auto_score"][dim])
            scores[name] = sum(vals) / len(vals) if vals else 0
        row = f"{dim_labels[dim]:<12}" + "".join(f"{scores[n]:>12.2f}" for n in backend_names)
        lines.append(row)

    # 汇总 LLM 裁判评分
    lines.append(f"\n{'─'*60}")
    lines.append("LLM 裁判评分汇总 (满分 5.0)")
    lines.append(f"{'─'*60}")

    judge_dims = ["accuracy", "terminology", "numbers", "fluency", "formatting"]
    dim_labels_j = {"accuracy": "准确性", "terminology": "术语", "numbers": "数字",
                    "fluency": "流畅度", "formatting": "格式"}

    header = f"{'维度':<14}" + "".join(f"{n:>12}" for n in backend_names)
    lines.append(header)
    lines.append("─" * len(header))

    for dim in judge_dims:
        scores = {}
        for name in backend_names:
            vals = []
            for r in results:
                t = r["translations"].get(name, {})
                for jname, js in t.get("judge_scores", {}).items():
                    if dim in js and isinstance(js[dim], (int, float)):
                        vals.append(js[dim])
            scores[name] = sum(vals) / len(vals) if vals else 0
        row = f"{dim_labels_j[dim]:<12}" + "".join(f"{scores[n]:>12.2f}" for n in backend_names)
        lines.append(row)

    # 速度对比
    lines.append(f"\n{'─'*60}")
    lines.append("翻译速度")
    lines.append(f"{'─'*60}")
    for name in backend_names:
        times = [r["translations"][name]["time"] for r in results
                 if name in r["translations"] and "time" in r["translations"][name]]
        if times:
            avg = sum(times) / len(times)
            lines.append(f"  {name:<12} 平均 {avg:.1f}s / 段")

    # 总分排名
    lines.append(f"\n{'─'*60}")
    lines.append("综合排名")
    lines.append(f"{'─'*60}")

    totals = {}
    for name in backend_names:
        auto_total = 0
        judge_total = 0
        count = 0
        for r in results:
            t = r["translations"].get(name, {})
            if "auto_score" in t:
                auto_total += sum(t["auto_score"].values()) / len(auto_dims)
            for jname, js in t.get("judge_scores", {}).items():
                vals = [js[d] for d in judge_dims if d in js and isinstance(js[d], (int, float))]
                if vals:
                    judge_total += sum(vals) / len(vals)
                    count += 1
        auto_avg = auto_total / len(results) if results else 0
        judge_avg = judge_total / count if count else 0
        # 综合分 = 自动评分(40%) + LLM评分(60%)
        totals[name] = auto_avg * 0.4 + (judge_avg / 5.0) * 0.6

    ranked = sorted(totals.items(), key=lambda x: x[1], reverse=True)
    for i, (name, score) in enumerate(ranked):
        medal = ["[1st]", "[2nd]", "[3rd]"][i] if i < 3 else f"[{i+1}th]"
        lines.append(f"  {medal} {name:<12} 综合分: {score:.3f}")

    # 逐样本对比
    lines.append(f"\n{'─'*60}")
    lines.append("逐样本翻译对比")
    lines.append(f"{'─'*60}")

    for i, r in enumerate(results):
        lines.append(f"\n--- 样本 {i+1}: {r['sample']['company']} ---")
        lines.append(f"原文: {r['sample']['text'][:120]}...")
        for name in backend_names:
            t = r["translations"].get(name, {})
            zh = t.get("zh", t.get("error", "N/A"))
            lines.append(f"  [{name}] {zh[:120]}...")

    lines.append(f"\n{'='*70}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="翻译质量评测")
    parser.add_argument("--samples", type=int, default=9, help="评测样本数")
    parser.add_argument("--judge", default="mimo", choices=["mimo", "minimax", "deepseek"],
                        help="裁判模型")
    args = parser.parse_args()

    cfg = load_config()
    run_evaluation(cfg, n_samples=args.samples, judge_name=args.judge)


if __name__ == "__main__":
    main()
