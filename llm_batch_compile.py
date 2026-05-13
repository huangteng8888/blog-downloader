#!/usr/bin/env python3
"""
Phase 3: LLM 批量编译流水线
从 top500 筛选结果 → 复制到 knowledge-base/raw/ → 调用 compile 引擎

用法:
  python llm_batch_compile.py                    # 全量 1500 篇
  python llm_batch_compile.py --limit 50         # 先跑 50 篇测试
  python llm_batch_compile.py --blogger 1215172700  # 只跑某个博主
"""

import os
import sys
import json
import shutil
import argparse
import re
from datetime import datetime
from pathlib import Path

# ------------------------------------------------------
# 路径配置
# ------------------------------------------------------
BLOGGER_OUTPUT = Path("/mnt/data/blog-downloader/output")
SCORED_DIR = Path("/mnt/data/blog-downloader/keyword_discovery/scored")
KB_RAW = Path("/home/ht/github/knowledge-base/raw")
KB_WIKI = Path("/home/ht/github/knowledge-base/wiki")
KB_PROCESS = Path("/home/ht/github/knowledge-base/process.py")

# 博主信息
BLOGGERS = {
    "1215172700": {"name": "缠中说禅", "top500": "1215172700_top500.json"},
    "1285707277": {"name": "股市风云", "top500": "1285707277_top500.json"},
    "1300871220": {"name": "徐小明",   "top500": "1300871220_top500.json"},
}


def make_slug(title: str, max_len: int = 60) -> str:
    """从标题生成安全的文件名 slug"""
    # 移除不安全字符
    slug = re.sub(r"[^\w\s-]", "", title)
    slug = re.sub(r"[-\s]+", "-", slug)
    slug = slug.strip("-").lower()
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("-")
    return slug or "untitled"


def load_top500(blogger_id: str) -> list[dict]:
    """加载某博主的 top500 列表"""
    path = SCORED_DIR / BLOGGERS[blogger_id]["top500"]
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def find_article_file(blogger_id: str, filename: str) -> Path | None:
    """在 output/ 目录中找到文章原始文件"""
    # filename 可能是 "486e105c0100a58f" 或 "486e105c0100a58f.md"
    stem = filename.replace(".md", "")
    candidates = [
        BLOGGER_OUTPUT / blogger_id / "posts" / f"{stem}.md",
        BLOGGER_OUTPUT / blogger_id / "posts" / filename if not filename.endswith(".md") else Path(),
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def stage_articles(articles: list[dict], blogger_id: str, blogger_name: str, dry_run: bool = False) -> list[tuple[Path, Path]]:
    """
    将文章复制到 knowledge-base/raw/，返回 (源文件, 目标文件) 对列表
    """
    KB_RAW.mkdir(parents=True, exist_ok=True)
    pairs = []

    for i, article in enumerate(articles):
        src = find_article_file(blogger_id, article["filename"])
        if src is None:
            print(f"  [WARN] 文件未找到: {article['filename']} (博主 {blogger_id})")
            continue

        # 生成目标文件名: {blogger}_{slug}_{rank}.md
        slug = make_slug(article["title"])
        rank = i + 1
        dst_name = f"{blogger_id}_{slug}_{rank:04d}.md"
        dst = KB_RAW / dst_name

        if not dry_run:
            # 注入 title 到文件顶部（让 process.py 能读到标题）
            title_line = f"# {article['title']}\n\n"
            with open(dst, "w", encoding="utf-8") as out:
                out.write(title_line)
                with open(src, "r", encoding="utf-8") as inp:
                    inp_content = inp.read()
                    # 避免重复写入已存在的title行
                    if not inp_content.startswith("# "):
                        out.write(inp_content)
                    else:
                        out.write(inp_content.lstrip("# ").lstrip())

        pairs.append((src, dst))

    return pairs


def call_compile_engine(limit: int | None = None, blogger_id: str | None = None, force: bool = False, dry_run: bool = False):
    """
    主流程: 加载top500 → 复制到raw → 调用knowledge-base compile
    """
    if not dry_run:
        os.environ.setdefault("MINIMAX_API_KEY", os.environ.get("MINIMAX_API_KEY", ""))
        if not os.environ.get("MINIMAX_API_KEY"):
            print("错误: MINIMAX_API_KEY 环境变量未设置")
            sys.exit(1)

    # 确定要处理的博主
    blogger_ids = [blogger_id] if blogger_id else list(BLOGGERS.keys())

    total_staged = 0
    total_compiled = 0

    for bid in blogger_ids:
        bname = BLOGGERS[bid]["name"]
        print(f"\n{'='*60}")
        print(f"处理博主: {bname} ({bid})")
        print(f"{'='*60}")

        articles = load_top500(bid)
        if limit:
            articles = articles[:limit]

        print(f"加载 {len(articles)} 篇文章")

        # stage 文件
        pairs = stage_articles(articles, bid, bname, dry_run=dry_run)
        print(f"已准备 {len(pairs)} 个文件")

        if not pairs:
            print("[INFO] 无文件可处理，跳过")
            continue

        # 调用 compile 引擎
        import subprocess
        for src, dst in pairs:
            total_staged += 1
            if dry_run:
                print(f"  [DRY] {dst.name}")
                continue

            # 调用 process.py --file {dst}
            # process.py 默认检查 front matter 是否已编译，已编译则跳过
            # 用 --force 强制重编译
            # 调用 process.py --file {dst}
            # process.py 依赖 python-frontmatter（未装时编译仍成功但exit 1）
            # 因此用 bash -c "python process.py ... || true" 忽略该错误，
            # 并以 wiki 文件是否生成为实际成功判断
            wiki_slug = dst.stem  # process.py 输出文件名同 stem
            wiki_file = KB_WIKI / f"{wiki_slug}.md"
            already_compiled = wiki_file.exists()

            if already_compiled and not force:
                print(f"  [SKIP] {dst.name}  (wiki 已存在)")
                continue

            cmd = f'{sys.executable} "{KB_PROCESS}" --file "{dst}"'
            if force:
                cmd = f'{sys.executable} "{KB_PROCESS}" --force --file "{dst}"'

            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                env={**os.environ, "MINIMAX_API_KEY": os.environ["MINIMAX_API_KEY"]},
                timeout=300,
            )
            # process.py --file 模式下 frontmatter 已存在会 exit 0（SKIP）
            # 成功判断：exit 0 或 wiki 文件已生成
            wiki_ok = wiki_file.exists()
            if result.returncode == 0 or wiki_ok:
                total_compiled += 1
                lines = result.stdout.strip().split("\n")
                last_line = [l for l in lines if l.strip()]
                if last_line:
                    print(f"  {last_line[-1]}")
                else:
                    print(f"  [OK] {dst.name} -> {wiki_file.name}")
            else:
                print(f"  [ERROR] {dst.name}: exit={result.returncode} {result.stderr[:100]}")

    print(f"\n{'='*60}")
    print(f"完成! 准备: {total_staged} | 编译: {total_compiled}")
    print(f"raw/: {KB_RAW} | wiki/: {KB_WIKI}")


def main():
    parser = argparse.ArgumentParser(description="Phase 3: LLM 批量编译")
    parser.add_argument("--limit", "-n", type=int, default=None, help="每博主限制篇数（测试用）")
    parser.add_argument("--blogger", "-b", type=str, default=None, choices=list(BLOGGERS.keys()), help="只处理特定博主")
    parser.add_argument("--force", "-f", action="store_true", help="强制重编译已有条目")
    parser.add_argument("--dry-run", action="store_true", help="只准备文件，不编译")
    args = parser.parse_args()

    call_compile_engine(
        limit=args.limit,
        blogger_id=args.blogger,
        force=args.force,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
