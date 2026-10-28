#!/usr/bin/env python3
"""
Phase 2: Multi-layer post scoring and filtering.
Uses the keyword dictionary to score all posts and rank them.

Usage: python post_scorer.py [--top N] [--output DIR]
"""

import os
import re
import json
from pathlib import Path
from collections import Counter
import argparse
import math
from typing import Optional

# ── Config ──────────────────────────────────────────────────────────────
BLOGGERS = {
    "1215172700": "缠中说禅",
    "1285707277": "股市风云",
    "1300871220": "徐小明",
}
# 项目根目录：优先取环境变量 BLOG_DOWNLOADER_ROOT，否则取本脚本所在目录
PROJECT_ROOT = Path(
    os.environ.get("BLOG_DOWNLOADER_ROOT", Path(__file__).resolve().parent)
)
BASE_DIR = PROJECT_ROOT / "output"
KEYWORD_FILE = PROJECT_ROOT / "keyword_discovery" / "stock_keywords_v1.json"
OUTPUT_DIR = PROJECT_ROOT / "keyword_discovery" / "scored"

DEFAULT_TOP = 500   # top N per blogger for LLM review

# ── Load keyword dict ───────────────────────────────────────────────────
def load_keywords() -> dict:
    with open(KEYWORD_FILE, encoding="utf-8") as f:
        return json.load(f)

def flatten_words(d: dict, excludes=("_comment",)) -> list[str]:
    """Flatten a nested keyword dict into a flat list."""
    words = []
    for k, v in d.items():
        if k in excludes:
            continue
        if isinstance(v, list):
            words.extend(v)
        elif isinstance(v, dict):
            words.extend(flatten_words(v))
    return words

# ── Text extraction ──────────────────────────────────────────────────────
def extract_text(filepath: Path) -> str:
    try:
        content = filepath.read_text(encoding="utf-8", errors="ignore")
        content = re.sub(r'^---\n.*?\n---\n', '', content, flags=re.DOTALL)
        content = re.sub(r'^article_id:.*?\n', '', content)
        content = re.sub(r'https?://\S+', ' ', content)
        content = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', content)
        content = re.sub(r'<[^>]+>', ' ', content)
        content = re.sub(r'[\r\n]+', ' ', content)
        return content.strip()
    except Exception:
        return ""

def get_post_date(text: str) -> Optional[str]:
    """Try to extract a date from post content."""
    # Look for patterns like 2005-08-01, 2005年08月01日, etc.
    patterns = [
        r'(\d{4}-\d{2}-\d{2})',
        r'(\d{4}年\d{1,2}月\d{1,2}日)',
        r'(\d{4}/\d{2}/\d{2})',
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            return m.group(1)
    return None

def get_title(text: str) -> str:
    """Get first meaningful line as title."""
    lines = text.split('\n')
    for line in lines:
        line = line.strip()
        if len(line) > 5:
            return line[:80]
    return "无标题"

# ── Scoring engine ───────────────────────────────────────────────────────
class Scorer:
    def __init__(self, keyword_dict: dict):
        self.kw = keyword_dict
        self.weights = keyword_dict["weights"]
        
        # Build flat word sets for fast lookup
        self.common_words = flatten_words(keyword_dict["common_core"])
        self.chan_words   = flatten_words(keyword_dict["chan_core"])
        self.gushi_words = flatten_words(keyword_dict["gushi_core"])
        self.xu_words    = flatten_words(keyword_dict["xu_core"])
        
        # Edge words
        edge = keyword_dict["edge_words"]
        self.chan_edge  = flatten_words(edge.get("chan_edge", {}))
        self.gushi_edge = flatten_words(edge.get("gushi_edge", {}))
        self.xu_edge    = flatten_words(edge.get("xu_edge", {}))
        
        print(f"Keyword loaded: common={len(self.common_words)}, "
              f"chan={len(self.chan_words)}, gushi={len(self.gushi_words)}, "
              f"xu={len(self.xu_words)}")

    def score(self, blogger_id: str, text: str) -> dict:
        text_lower = text.lower()
        text_nows = text.replace('\n', ' ').replace(' ', '')
        
        scores = {}
        word_hits = {}
        
        # 长度归一化：用 sqrt(len) 而非 len 作分母。
        # 原实现 `命中数 / len * 10000` 会让越短的文章得分越高 —— 实测徐小明
        # top500 中 41.2% 文章不足 150 字，真正有理论含量的长文被挤出。
        # sqrt 归一化后，"76 字命中 5 个词"与"800 字命中 15 个词"已基本可比。
        # 1. Common core score
        common_hits = sum(1 for w in self.common_words if w in text_lower)
        scores["common"] = common_hits / max(len(text), 1) ** 0.5 * 100 * self.weights["common_core"]
        word_hits["common"] = common_hits
        
        # 2. Personal core score (varies by blogger)
        if blogger_id == "1215172700":
            personal = self.chan_words
            personal_name = "chan"
        elif blogger_id == "1285707277":
            personal = self.gushi_words
            personal_name = "gushi"
        else:
            personal = self.xu_words
            personal_name = "xu"
        
        personal_hits = sum(1 for w in personal if w in text_lower)
        scores["personal"] = personal_hits / max(len(text), 1) ** 0.5 * 100 * self.weights["personal_core"]
        word_hits["personal"] = personal_hits
        
        # 3. Edge penalty
        if blogger_id == "1215172700":
            edge_hits = sum(1 for w in self.chan_edge if w in text_lower)
        elif blogger_id == "1285707277":
            edge_hits = sum(1 for w in self.gushi_edge if w in text_lower)
        else:
            edge_hits = sum(1 for w in self.xu_edge if w in text_lower)
        
        scores["edge_penalty"] = edge_hits * self.weights["edge_penalty"]
        word_hits["edge"] = edge_hits
        
        # 4. Live stream detection (special penalty)
        live_words = ["直播", "盘中直播", "盘中", "收盘简评"]
        live_hits = sum(1 for w in live_words if w in text_lower)
        scores["live_penalty"] = live_hits * -0.3 if blogger_id in ("1285707277", "1300871220") else 0
        
        # 5. Length check（原为 <200 字一律打 0.5 折，惩罚过重且与分母效应叠加；
        #    改为分档，只对真正的碎片化短贴降权）
        text_len = len(text)
        if text_len < 100:
            scores["length_factor"] = 0.7      # 一句话碎片
        elif text_len < 250:
            scores["length_factor"] = 0.9
        elif text_len < 50000:
            scores["length_factor"] = 1.0
        else:
            scores["length_factor"] = 0.8      # 异常超长
        
        # 6. Title/keyword combo boost (if has theory word in title area)
        title_area = text[:500].lower()
        theory_in_title = sum(1 for w in personal if w in title_area) if blogger_id else 0
        scores["title_boost"] = theory_in_title * 0.5
        
        # Total
        raw_total = (
            scores["common"] + 
            scores["personal"] * self.weights["personal_boost"] +
            scores["edge_penalty"] +
            scores["live_penalty"] +
            scores["title_boost"]
        ) * scores["length_factor"]
        
        return {
            "raw_score": round(raw_total, 4),
            "common_score": round(scores["common"], 4),
            "personal_score": round(scores["personal"], 4),
            "edge_penalty": round(scores["edge_penalty"], 4),
            "live_penalty": round(scores["live_penalty"], 4),
            "length_factor": round(scores["length_factor"], 2),
            "word_hits": word_hits,
        }

# ── Processing ────────────────────────────────────────────────────────────
def load_index_meta(blogger_id: str) -> dict:
    """从 posts/index.json 读取权威元数据：article_id -> {pub_time, pub_date, title}

    原实现用 get_post_date() 在正文里正则找日期，但 v2 正文不含日期
    （日期只在 index.json 的 pub_time 里），导致 date 缺失率高达 93.6%~100%。
    """
    index_path = BASE_DIR / blogger_id / "posts" / "index.json"
    meta = {}
    if not index_path.exists():
        print(f"  [WARN] {index_path} not found，date/title 将回退为正文推断")
        return meta
    data = json.loads(index_path.read_text(encoding='utf-8'))
    for p in data.get('posts', []):
        aid = p.get('article_id')
        if aid:
            meta[aid] = {
                'pub_time': p.get('pub_time', ''),
                'pub_date': p.get('pub_date', ''),
                'title': p.get('title', ''),
                'tags': p.get('tags', []),
            }
    return meta


def process_blogger(blogger_id: str, blogger_name: str, scorer: Scorer, top_n: int):
    posts_dir = BASE_DIR / blogger_id / "posts"
    if not posts_dir.exists():
        print(f"  [WARN] {posts_dir} not found, skipping")
        return []

    index_meta = load_index_meta(blogger_id)
    print(f"  index.json 元数据: {len(index_meta)} 条")

    results = []
    files = list(posts_dir.glob("*.md"))
    files = [f for f in files if f.name != "index.json"]

    print(f"\n  Scoring {len(files)} posts...")

    for i, f in enumerate(files):
        text = extract_text(f)
        if len(text) < 50:
            continue

        score_result = scorer.score(blogger_id, text)

        # 优先用 index.json 的权威元数据，缺失时才回退到正文推断
        im = index_meta.get(f.stem, {})
        title = im.get('title') or get_title(text)
        date = im.get('pub_date') or get_post_date(text)

        results.append({
            "filename": f.name,
            "article_id": f.stem,
            "blogger_id": blogger_id,
            "blogger_name": blogger_name,
            "title": title,
            "date": date,
            "pub_time": im.get('pub_time', ''),
            "tags": im.get('tags', []),
            "text_len": len(text),
            "preview": text[:200].replace('\n', ' ').strip(),
            **score_result,
        })

        if (i + 1) % 1000 == 0:
            print(f"    {i+1}/{len(files)} processed...")

    # Sort by score
    results.sort(key=lambda x: -x["raw_score"])
    
    # Print top 20 for inspection
    print(f"\n  Top 20 posts for {blogger_name}:")
    print(f"  {'Rank':>4} {'Score':>8} {'Len':>6} {'Common':>7} {'Persnl':>7} {'Edge':>5} {'Title'}")
    print(f"  {'-'*80}")
    for i, r in enumerate(results[:20]):
        title = r["title"][:38] if len(r["title"]) > 38 else r["title"]
        print(f"  {i+1:4d} {r['raw_score']:8.3f} {r['text_len']:6d} "
              f"{r['common_score']:7.2f} {r['personal_score']:7.2f} {r['edge_penalty']:5.1f}  {title}")
    
    return results


def main():
    parser = argparse.ArgumentParser(description="Score and filter blog posts")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP, help=f"Top N per blogger (default {DEFAULT_TOP})")
    parser.add_argument("--output", default=str(OUTPUT_DIR), help="Output directory")
    parser.add_argument("--blogger", default=None, help="Only process specific blogger ID")
    args = parser.parse_args()
    
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    scorer = Scorer(load_keywords())
    
    all_results = {}
    total_selected = 0
    
    bloggers_to_process = [(k, v) for k, v in BLOGGERS.items() 
                            if args.blogger is None or args.blogger == k]
    
    for blogger_id, blogger_name in bloggers_to_process:
        print(f"\n{'='*60}")
        print(f"Processing: {blogger_name} ({blogger_id})")
        print(f"{'='*60}")
        
        results = process_blogger(blogger_id, blogger_name, scorer, args.top)
        
        # Save full ranked list
        out_file = OUTPUT_DIR / f"{blogger_id}_ranked.json"
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\n  Saved ranked list: {out_file}")
        
        # Save top N for LLM review
        top_n = args.top
        selected = results[:top_n]
        sel_file = OUTPUT_DIR / f"{blogger_id}_top{top_n}.json"
        with open(sel_file, "w", encoding="utf-8") as f:
            json.dump(selected, f, ensure_ascii=False, indent=2)
        print(f"  Saved top {top_n}: {sel_file}")
        
        # Stats
        all_scored = len(results)
        avg_score = sum(r["raw_score"] for r in results) / max(all_scored, 1)
        selected_avg = sum(r["raw_score"] for r in selected) / max(len(selected), 1)
        print(f"\n  Stats: {all_scored} scored | avg={avg_score:.3f} | "
              f"top{top_n}_avg={selected_avg:.3f}")
        
        all_results[blogger_id] = {
            "blogger_name": blogger_name,
            "total": all_scored,
            "avg_score": round(avg_score, 4),
            "selected_count": len(selected),
            "selected_avg": round(selected_avg, 4),
            "files": {
                "ranked": str(out_file),
                "selected": str(sel_file),
            }
        }
        total_selected += len(selected)
    
    # Summary
    summary_file = OUTPUT_DIR / "scoring_summary.json"
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    
    print(f"\n{'='*60}")
    print(f"SCORING COMPLETE")
    print(f"Total posts scored: {sum(v['total'] for v in all_results.values()):,}")
    print(f"Total selected for LLM review: {total_selected}")
    print(f"Summary: {summary_file}")
    print(f"{'='*60}")
    
    # Print final table
    print(f"\n{'Blogger':<15} {'Total':>7} {'AvgScore':>9} {'Selected':>9} {'SelAvg':>9}")
    print("-" * 53)
    for bid, info in all_results.items():
        print(f"{info['blogger_name']:<15} {info['total']:>7,} "
              f"{info['avg_score']:>9.3f} {info['selected_count']:>9} "
              f"{info['selected_avg']:>9.3f}")


if __name__ == "__main__":
    main()
