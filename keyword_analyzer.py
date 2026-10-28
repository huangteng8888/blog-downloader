#!/usr/bin/env python3
"""
Phase 1: TF-IDF keyword discovery for blog posts.
Scans all posts from 3 bloggers, extracts high-frequency meaningful words
to build personal core keyword dictionaries.

Usage: python keyword_analyzer.py
"""

import os
import re
import json
from pathlib import Path
from collections import Counter
import math
import jieba
import jieba.analyse

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
OUTPUT_DIR = PROJECT_ROOT / "keyword_discovery"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ===== Step 1: 已知股票核心词（公用 + 初始个人词）=====
# 这些词会在TF-IDF时加权，让它们更容易被识别

SEED_KEYWORDS = {
    "common": {
        "走势", "中枢", "背驰", "笔", "线段", "级别", "缠论", "买点", "卖点",
        "止损", "止盈", "仓位", "建仓", "清仓", "持仓", "趋势", "支撑", "压力",
        "突破", "回踩", "背离", "金叉", "死叉", "MACD", "KDJ", "RSI", "均线",
        "K线", "成交量", "换手率", "涨停", "跌停", "波浪", "推动浪", "调整浪",
        "趋势线", "突破", "跌破", "量价", "市盈率", "市净率", "ROE", "财报",
        "估值", "收益", "涨停板", "跌停板", "筹码", "庄家", "主力", "散户",
        "分时", "日线", "周线", "月线", "年线", "抄底", "逃顶", "波段",
        "震荡", "整理", "反转", "回调", "反弹", "横盘", "放量", "缩量",
    },
    "chan": {
        "走势中枢", "背驰", "笔", "线段", "级别", "缠论", "第一买卖点", "第二买卖点",
        "第三买卖点", "中枢震荡", "走势类型", "趋势转折", "盘整", "趋", "背驰",
        "反弹", "回调", "次级别", "本级别", "中枢移动", "走势终完美",
    },
    "xu": {
        "波浪", "波浪理论", "推动浪", "调整浪", "浪级", "左侧交易", "右侧交易",
        "量化", "钝化", "九循环", "时间周期", "空间", "结构", "趋势", "浪",
    },
    "gushi": {
        # 待扫描后确定
    }
}

EDGE_KEYWORDS = {
    "common": [
        "诗词", "诗歌", "七律", "五律", "词曰", "诗曰", "菩萨蛮", "沁园春",
        "满江红", "浣溪沙", "蝶恋花", "水调歌头", "微博", "回复", "转载",
        "本ID", "新浪", "博客", "点击进入", "更多文章", "视频", "音乐",
        "旅游", "钓鱼", "喝酒", "温泉", "应酬", "吃饭", "休息", "大家好",
    ],
    "chan": [
        "禅宗", "佛教", "佛法", "释迦", "达摩", "六祖", "慧能", "禅", "公案",
        "打坐", "修行", "开悟", "明心见性", "涅槃", "般若", "金刚经", "心经",
        "布什", "美国", "反恐", "911", "台湾", "政治", "文革", "革命",
    ],
    "xu": [
        "直播", "收盘", "大盘", "今日", "明天", "本周", "本年", "快讯",
    ]
}


def extract_article_text(filepath: Path) -> str:
    """Extract clean text from a blog markdown file."""
    try:
        content = filepath.read_text(encoding="utf-8", errors="ignore")
        # Remove frontmatter
        content = re.sub(r'^---\n.*?\n---\n', '', content, flags=re.DOTALL)
        content = re.sub(r'^article_id:.*?\n', '', content)
        # Remove image URLs
        content = re.sub(r'https?://\S+', '', content)
        # Remove markdown links
        content = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', content)
        # Remove HTML tags
        content = re.sub(r'<[^>]+>', '', content)
        return content.strip()
    except Exception as e:
        return ""


def load_posts(blogger_id: str) -> list[tuple[str, str]]:
    """Load all posts for a blogger. Returns list of (filename, text)."""
    posts_dir = BASE_DIR / blogger_id / "posts"
    if not posts_dir.exists():
        return []
    posts = []
    for f in posts_dir.glob("*.md"):
        if f.name == "index.json":
            continue
        text = extract_article_text(f)
        if text:
            posts.append((f.name, text))
    return posts


def compute_tfidf(posts: list[tuple[str, str]], top_k: int = 200) -> list[tuple[str, float]]:
    """Compute TF-IDF for a corpus of posts."""
    # Use jieba's TF-IDF algorithm
    all_text = "\n".join(text for _, text in posts)
    
    # Extract top keywords by TF-IDF
    keywords = jieba.analyse.extract_tags(all_text, topK=top_k, withWeight=True)
    return keywords


def compute_word_freq(posts: list[tuple[str, str]], word_list: list[str]) -> Counter:
    """Count frequency of specific words across all posts."""
    freq = Counter()
    for _, text in posts:
        text_lower = text.lower()
        for word in word_list:
            count = text_lower.count(word.lower())
            if count > 0:
                freq[word] += 1
    return freq


def analyze_blogger(blogger_id: str, blogger_name: str) -> dict:
    """Full analysis for one blogger."""
    print(f"\n{'='*60}")
    print(f"Analyzing: {blogger_name} ({blogger_id})")
    print(f"{'='*60}")
    
    posts = load_posts(blogger_id)
    print(f"Loaded {len(posts)} posts")
    
    if not posts:
        return {}
    
    # Sample text for context
    sample_chars = sum(len(text) for _, text in posts)
    print(f"Total characters: {sample_chars:,}")
    
    # TF-IDF top keywords
    print("\nComputing TF-IDF keywords (top 200)...")
    tfidf_keywords = compute_tfidf(posts, top_k=200)
    
    print("\nTop 50 TF-IDF keywords:")
    for i, (word, weight) in enumerate(tfidf_keywords[:50]):
        print(f"  {i+1:3d}. {word:20s} {weight:.4f}")
    
    # Check seed keyword frequency
    all_seed_words = set()
    for category, words in SEED_KEYWORDS.items():
        all_seed_words.update(words)
    
    seed_freq = compute_word_freq(posts, list(all_seed_words))
    seed_freq_sorted = sorted(seed_freq.items(), key=lambda x: -x[1])
    
    print("\nSeed keyword hits (non-zero):")
    for word, count in seed_freq_sorted:
        if count > 0:
            pct = count / len(posts) * 100
            print(f"  {word:15s}: {count:5d} posts ({pct:.1f}%)")
    
    # Posts mentioning stock indicators (rough check)
    stock_indicator_words = ["MACD", "KDJ", "RSI", "均线", "K线", "成交量", "缠论", "波浪"]
    indicator_freq = compute_word_freq(posts, stock_indicator_words)
    indicator_posts = sum(1 for _, text in posts if any(
        w.lower() in text.lower() for w in stock_indicator_words
    ))
    print(f"\nPosts mentioning stock indicators: {indicator_posts} ({indicator_posts/len(posts)*100:.1f}%)")
    
    return {
        "blogger_id": blogger_id,
        "blogger_name": blogger_name,
        "post_count": len(posts),
        "total_chars": sample_chars,
        "tfidf_top50": tfidf_keywords[:50],
        "seed_keyword_hits": dict(seed_freq),
        "indicator_post_count": indicator_posts,
    }


def main():
    print("=" * 60)
    print("BLOG KEYWORD DISCOVERY - PHASE 1")
    print("=" * 60)
    
    all_results = {}
    
    for blogger_id, blogger_name in BLOGGERS.items():
        result = analyze_blogger(blogger_id, blogger_name)
        all_results[blogger_id] = result
    
    # Save results
    results_file = OUTPUT_DIR / "phase1_tfidf_results.json"
    with open(results_file, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    
    print(f"\n{'='*60}")
    print("PHASE 1 COMPLETE")
    print(f"Results saved to: {results_file}")
    print(f"{'='*60}")
    
    # Print summary table
    print("\nSUMMARY:")
    print(f"{'Blogger':<15} {'Posts':>8} {'Stock%':>8} {'Top Word':<20}")
    print("-" * 55)
    for blogger_id, name in BLOGGERS.items():
        r = all_results.get(blogger_id, {})
        count = r.get("post_count", 0)
        stock_pct = r.get("indicator_post_count", 0) / count * 100 if count > 0 else 0
        top_word = r.get("tfidf_top50", [("", 0)])[0][0] if r.get("tfidf_top50") else ""
        print(f"{name:<15} {count:>8} {stock_pct:>7.1f}%  {top_word:<20}")


if __name__ == "__main__":
    main()
