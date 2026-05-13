#!/usr/bin/env python3
"""
Async Batch Compile - Phase 3 加速版
将多篇文章聚合到单次 LLM API 调用，并发执行

原理:
  - 每批 BATCH_SIZE=6 篇文章聚合为 1 次 API 请求（32K context 安全）
  - CONCURRENCY=10 批次并发执行
  - 500 篇缠中说禅: 84 批 / 10 并发 = ~9 批次 ≈ 45-90 秒纯 API 时间

用法:
  python async_batch_compile.py --blogger 1215172700          # 缠中说禅全量 500
  python async_batch_compile.py --blogger 1215172700 -n 50    # 测试 50 篇
  python async_batch_compile.py --blogger 1285707277 -n 100   # 股市风云 top100
  python async_batch_compile.py --all                               # 三博主全量
"""

import os
import sys
import json
import asyncio
import re
import time
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiohttp

# ------------------------------------------------------
# 配置
# ------------------------------------------------------
SCORED_DIR  = Path("/mnt/data/blog-downloader/keyword_discovery/scored")
BLOGGER_OUTPUT = Path("/mnt/data/blog-downloader/output")
KB_RAW    = Path("/home/ht/github/knowledge-base/raw")
KB_WIKI   = Path("/home/ht/github/knowledge-base/wiki")
KB_CONFIG = Path("/home/ht/github/knowledge-base/config.py")

BATCH_SIZE    = 6      # 每批文章数（32K context 安全值）
CONCURRENCY   = 10     # 并发批次上限
API_TIMEOUT   = 180    # 单次 API 超时（秒）
MAX_RETRIES  = 2      # 失败重试次数
TARGET_DIRS   = {      # 三博主 topN 规划
    "1215172700": {"name": "缠中说禅", "topN": 1000},
    "1285707277": {"name": "股市风云", "topN": 100},
    "1300871220": {"name": "徐小明",   "topN": 500},
}

BLOGGER_NAMES = {bid: v["name"] for bid, v in TARGET_DIRS.items()}

# ------------------------------------------------------
# MiniMax API
# ------------------------------------------------------
def get_api_config():
    """从环境变量和 knowledge-base config 获取 API 配置"""
    api_key = os.environ.get("MINIMAX_API_KEY") or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        # 尝试从 hermes .env 读取
        try:
            with open(Path.home() / ".hermes/.env") as f:
                for line in f:
                    if line.startswith("MINIMAX_API_KEY="):
                        api_key = line.split("=", 1)[1].strip()
                        break
        except Exception:
            pass

    base_url = os.environ.get("MINIMAX_BASE_URL", "https://api.minimax.chat/anthropic/v1")
    return api_key, base_url


def build_wiki_system_prompt() -> str:
    """构建 wiki 编译的系统提示词"""
    return """你是一个专业的缠论知识库编辑，擅长将股市博文编译成结构化的 wiki Markdown。

## 你的职责
1. 将每篇博文整理成独立的 wiki 条目
2. 每个条目必须包含完整的 YAML front matter
3. 条目内容要有清晰的知识结构，不是简单摘要

## 输出格式（每个条目）
---
title: "条目标题"
summary: "简短摘要，1-2句话"
tags: [tag1, tag2, tag3]
created: "YYYY-MM-DD"
related: [相关条目slug1, 相关条目slug2]
---
# 条目标题

## 知识要点
（整理的核心内容）

---
（下一个条目...）

## 重要规则
- tags 最多 5 个，使用中文标签如：缠论、中枢、背驰、笔、线段、波浪理论、调整浪
- related 最多 3 个，用中文概念词如：背驰、中枢、趋势
- related 禁止用自由文本，必须是有具体知识含量的概念
- 每篇文章独立一个条目 section
- 保持原文的技术术语准确性"""


def build_batch_user_prompt(articles: list[dict]) -> str:
    """将多篇文章构建为单次 API 的 user prompt"""
    lines = [
        "请将以下文章编译成独立的 wiki 条目。\n",
        "## 输出格式\n",
        "返回 JSON 数组，每篇文章一个对象：\n",
        '[{"title": "...", "summary": "...", "tags": ["缠论", "中枢"], "related": ["背驰", "趋势"], "content": "Markdown 正文（以条目标题作为一级标题）"}, ...]\n',
        "## 要求\n",
        "- tags 最多 5 个，related 最多 3 个\n",
        "- content 是纯 Markdown，不含 front matter\n",
        "- 严格按顺序输出 N 个条目（N = 文章数量）\n",
        "- tags 用中文：缠论、中枢、背驰、笔、线段、波浪理论、调整浪、趋势、盘整\n\n",
        f"共 {len(articles)} 篇文章：\n",
    ]
    for i, art in enumerate(articles):
        lines.append(f"=== 文章 {i+1} ===")
        lines.append(f"标题: {art['title']}")
        lines.append(f"正文: {art['content'][:2500]}")
        lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------
# 异步 HTTP 请求
# ------------------------------------------------------
async def call_minimax_batch(
    session: aiohttp.ClientSession,
    api_key: str,
    base_url: str,
    articles: list[dict],
    semaphore: asyncio.Semaphore,
    batch_idx: int,
) -> tuple[int, list[dict], str]:
    """
    发送一批文章到 MiniMax API，返回 (成功数, 条目列表, 错误信息)
    """
    async with semaphore:
        url = f"{base_url}/messages"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }

        system = build_wiki_system_prompt()
        user_prompt = build_batch_user_prompt(articles)

        payload = {
            "model": "MiniMax-M2.7",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": 32768,
        }

        for attempt in range(MAX_RETRIES):
            try:
                async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=API_TIMEOUT)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        text = extract_text_from_response(data)
                        entries = parse_batch_response(text, articles)
                        return len(entries), entries, ""
                    elif resp.status == 429:
                        # Rate limit，等一等再试
                        await asyncio.sleep(5 * (attempt + 1))
                        continue
                    else:
                        text = await resp.text()
                        return 0, [], f"HTTP {resp.status}: {text[:200]}"
            except asyncio.TimeoutError:
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                return 0, [], f"Timeout after {API_TIMEOUT}s"
            except Exception as e:
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                return 0, [], str(e)

        return 0, [], "Max retries exceeded"


def extract_text_from_response(data: dict) -> str:
    """从 MiniMax API 响应中提取文本"""
    parts = []
    for block in data.get("content", []):
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts)


# ------------------------------------------------------
# 响应解析
# ------------------------------------------------------
def parse_batch_response(text: str, articles: list[dict]) -> list[dict]:
    """
    解析 LLM 返回的批量 wiki 文本。
    优先尝试 JSON 解析（精确、按顺序），fallback 到 --- 分割。
    """
    # ---- 方法1: JSON 解析 ----
    entries = []

    # 尝试从文本中提取 JSON 数组
    # LLM 可能把 JSON 包裹在 markdown code block 或直接输出
    json_text = text.strip()

    # 去掉可能的 markdown 代码块
    if json_text.startswith("```"):
        lines = json_text.split("\n")
        json_text = "\n".join(lines[1:])  # 去掉第一行 ```
        if json_text.endswith("```"):
            json_text = json_text[:-3].strip()

    # 尝试找到 JSON 数组开始和结束
    arr_start = json_text.find("[")
    arr_end = json_text.rfind("]")

    if arr_start != -1 and arr_end != -1 and arr_end > arr_start:
        try:
            candidate = json_text[arr_start:arr_end+1]
            parsed = json.loads(candidate)
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict) and "title" in item:
                        entries.append({
                            "title":   str(item.get("title", "")),
                            "summary": str(item.get("summary", "")),
                            "tags":    [str(t) for t in item.get("tags", []) if t][:5],
                            "related":  [str(r) for r in item.get("related", []) if r][:3],
                            "body":    item.get("content", "") or item.get("body", ""),
                        })
                if entries:
                    return entries
        except json.JSONDecodeError:
            pass

    # ---- 方法2: --- 分隔符 fallback ----
    text = json_text.strip()
    if text.startswith("---"):
        text = text[3:].strip()

    raw_sections = text.split("\n---")
    seen_titles: dict[str, bool] = {}

    for sec in raw_sections:
        sec = sec.strip()
        if not sec:
            continue

        entry = parse_single_entry(sec, articles)
        if not entry:
            continue

        # 去重
        norm_title = re.sub(r"\s+", "", entry["title"])
        if norm_title in seen_titles:
            continue
        seen_titles[norm_title] = True

        # 匹配原始文章
        for i, art in enumerate(articles):
            art_norm = re.sub(r"\s+", "", art["title"])
            if norm_title == art_norm or art_norm in norm_title or norm_title in art_norm:
                entry["title"] = art["title"]
                entry["_article_idx"] = i
                break

        entries.append(entry)

    return entries


def parse_single_entry(section: str, articles: list[dict]) -> Optional[dict]:
    """解析单个 wiki 条目"""
    lines = section.split("\n")

    # 提取 front matter（如果有）
    in_fm = False
    fm_lines = []
    body_lines = []
    found_title_in_fm = False

    for line in lines:
        if line.strip() == "---":
            if not in_fm:
                in_fm = True
                continue
            else:
                # front matter 结束
                in_fm = False
                continue
        if in_fm:
            fm_lines.append(line)
        else:
            body_lines.append(line)

    # 解析 front matter
    meta = {"title": "", "summary": "", "tags": [], "related": []}
    for line in fm_lines:
        line = line.strip()
        if line.startswith("title:"):
            meta["title"] = line.split(":", 1)[1].strip().strip('"').strip("'")
        elif line.startswith("summary:"):
            meta["summary"] = line.split(":", 1)[1].strip().strip('"').strip("'")
        elif line.startswith("tags:"):
            tag_part = line.split(":", 1)[1].strip().strip("[]")
            meta["tags"] = [t.strip().strip('"').strip("'") for t in tag_part.split(",") if t.strip()]
        elif line.startswith("related:"):
            rel_part = line.split(":", 1)[1].strip().strip("[]")
            meta["related"] = [t.strip().strip('"').strip("'") for t in rel_part.split(",") if t.strip()]

    # 如果 front matter 没有 title，尝试从 markdown 标题提取
    body_text = "\n".join(body_lines)
    if not meta["title"]:
        for line in body_lines[:10]:
            m = re.match(r'^#+\s+(.+)$', line.strip())
            if m:
                meta["title"] = m.group(1).strip().strip('"').strip("'")
                break

    # 如果还是没有 title，用文章标题
    if not meta["title"] and articles:
        meta["title"] = articles[0].get("title", "无标题")

    if not meta["summary"]:
        meta["summary"] = f"关于 {meta['title']} 的知识条目"

    if not meta["tags"]:
        meta["tags"] = ["待分类"]

    return {
        "title":   meta["title"],
        "summary": meta["summary"],
        "tags":    meta["tags"][:5],
        "related": meta["related"][:3],
        "body":    body_text.strip(),
    }


# ------------------------------------------------------
# 文件写入
# ------------------------------------------------------
def make_wiki_slug(title: str) -> str:
    """从标题生成 wiki 文件名"""
    slug = re.sub(r"[^\w\s-]", "", title)
    slug = re.sub(r"[-\s]+", "-", slug)
    slug = slug.strip("-").lower()
    return slug or "untitled"


def make_front_matter(title: str, summary: str, tags: list, related: list) -> str:
    """生成 YAML front matter"""
    lines = ["---"]
    lines.append(f'title: "{title}"')
    lines.append(f'summary: "{summary}"')
    lines.append(f"tags: [{', '.join(tags)}]")
    lines.append(f'created: "{datetime.now(timezone.utc).strftime("%Y-%m-%d")}"')
    if related:
        lines.append(f"related: [{', '.join(related)}]")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def write_wiki_entry(entry: dict, blogger_id: str, wiki_slug: str) -> Path:
    """将条目写入 wiki 目录（已存在则跳过）"""
    KB_WIKI.mkdir(parents=True, exist_ok=True)

    # 文件名：blogger_id_slug_rank.md
    base_slug = f"{blogger_id}_{wiki_slug}"
    dst = KB_WIKI / f"{base_slug}.md"

    # 避免文件名冲突（找下一个可用编号）
    counter = 1
    while dst.exists():
        dst = KB_WIKI / f"{base_slug}_{counter}.md"
        counter += 1

    front_matter = make_front_matter(
        entry["title"],
        entry["summary"],
        entry["tags"],
        entry["related"],
    )

    with open(dst, "w", encoding="utf-8") as f:
        f.write(front_matter)
        f.write(entry["body"])
        f.write("\n")

    return dst


# ------------------------------------------------------
# 核心异步编排
# ------------------------------------------------------
async def compile_blogger(
    session: aiohttp.ClientSession,
    api_key: str,
    base_url: str,
    blogger_id: str,
    topN: int,
    semaphore: asyncio.Semaphore,
    progress_callback=None,
) -> dict:
    """编译单个博主 topN 篇文章"""
    name = BLOGGER_NAMES[blogger_id]

    # 加载 topN 列表
    top_file = SCORED_DIR / f"{blogger_id}_top500.json"
    with open(top_file, "r", encoding="utf-8") as f:
        all_articles = json.load(f)

    articles = all_articles[:topN]
    total = len(articles)
    print(f"[{name}] 共 {total} 篇，分 {len(articles)//BATCH_SIZE + (1 if total%BATCH_SIZE else 0)} 批")

    # 读取每篇文章的正文内容
    print(f"[{name}] 加载文章正文...")
    articles_with_content = []
    for i, art in enumerate(articles):
        stem = art["filename"].replace(".md", "")
        src_path = BLOGGER_OUTPUT / blogger_id / "posts" / f"{stem}.md"
        if src_path.exists():
            content = src_path.read_text(encoding="utf-8")
            # 去掉 front matter (article_id: ...)
            content_clean = re.sub(r'^---\narticle_id:.*?\n---\n', '', content, flags=re.DOTALL)
            # 从正文第一行提取真实标题（去掉 # 号）
            first_line = content_clean.strip().split('\n')[0] if content_clean.strip() else art["title"]
            real_title = first_line.lstrip('#').strip() if first_line else art["title"]
            # 取前100字作为标题（原文可能是长句）
            title = real_title[:100] if len(real_title) > 100 else (real_title or art["title"])
            content = content_clean[:3000]
        else:
            content = art.get("preview", "") or ""
            title = art["title"]

        articles_with_content.append({
            "idx":    i,
            "title":  title,
            "content": content,
            "raw_score": art.get("raw_score", 0),
        })

    # 分批
    batches = []
    for i in range(0, len(articles_with_content), BATCH_SIZE):
        batches.append(articles_with_content[i:i+BATCH_SIZE])

    results = {"success": 0, "failed": 0, "skipped": 0, "entries": []}

    async def process_batch(batch_idx: int, batch: list):
        wiki_slug = make_wiki_slug(batch[0]["title"])[:40]
        success_cnt, entries, err = await call_minimax_batch(
            session, api_key, base_url, batch, semaphore, batch_idx
        )

        if err:
            print(f"  [BATCH {batch_idx+1}] 失败: {err[:80]}")
            results["failed"] += len(batch)
            return

        # 写入每个条目
        for entry in entries:
            try:
                slug = make_wiki_slug(entry["title"])[:50]
                # 检查 wiki 文件是否已存在（同名 slug 跳过，避免重复）
                base_slug = f"{blogger_id}_{slug}"
                existing = KB_WIKI / f"{base_slug}.md"
                skip = False
                n = 1
                while existing.exists():
                    n += 1
                    existing = KB_WIKI / f"{base_slug}_{n}.md"
                # n==1: xxx.md 不存在，直接写
                # n>1:  xxx.md 存在，但 xxx_2.md...xxx_{n-1}.md 也存在，xxx_N.md 是下一个可用名
                # 但如果 xxx.md 存在，说明这篇文章已编译过，跳过
                if (KB_WIKI / f"{base_slug}.md").exists():
                    skip = True
                if skip:
                    results["skipped"] += 1
                    continue
                dst = write_wiki_entry(entry, blogger_id, slug)
                print(f"  [OK] {dst.name}")
                results["success"] += 1
                results["entries"].append(entry)
            except Exception as e:
                print(f"  [WARN] 写入失败: {e}")
                results["failed"] += 1

    # 并发执行所有批次
    batch_tasks = [process_batch(i, b) for i, b in enumerate(batches)]
    await asyncio.gather(*batch_tasks)

    print(f"[{name}] 完成: 成功 {results['success']}, 失败 {results['failed']}")
    return results


async def run_all(specific_blogger: Optional[str] = None, limit_per_blogger: Optional[int] = None):
    """运行所有博主或指定博主"""
    api_key, base_url = get_api_config()
    if not api_key:
        print("错误: MINIMAX_API_KEY 未设置")
        sys.exit(1)

    print(f"[INFO] API: {base_url}")
    print(f"[INFO] Batch: {BATCH_SIZE} 篇/批, 并发: {CONCURRENCY}")
    print(f"[INFO] Key prefix: {api_key[:12]}")
    print()

    semaphore = asyncio.Semaphore(CONCURRENCY)

    async with aiohttp.ClientSession() as session:
        bloggers = [specific_blogger] if specific_blogger else list(TARGET_DIRS.keys())

        for bid in bloggers:
            cfg = TARGET_DIRS[bid]
            topN = limit_per_blogger if limit_per_blogger else cfg["topN"]
            print(f"\n{'='*60}")
            print(f"开始: {cfg['name']} (top {topN})")
            print(f"{'='*60}")

            await compile_blogger(
                session, api_key, base_url, bid, topN, semaphore
            )


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Async Batch Compile - LLM 加速编译")
    parser.add_argument("--blogger", "-b", choices=list(TARGET_DIRS.keys()), help="指定博主")
    parser.add_argument("--all", "-a", action="store_true", help="三博主全量")
    parser.add_argument("-n", "--limit", type=int, default=None, help="每博主限制篇数（测试用）")
    args = parser.parse_args()

    if not args.all and not args.blogger:
        parser.print_help()
        sys.exit(1)

    asyncio.run(run_all(
        specific_blogger=args.blogger if not args.all else None,
        limit_per_blogger=args.limit,
    ))


if __name__ == "__main__":
    main()
