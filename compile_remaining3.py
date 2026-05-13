#!/usr/bin/env python3
"""编译缠中说禅未编译帖子（用内容指纹去重）"""
import asyncio, aiohttp, json, os, re, sys
from pathlib import Path
from datetime import datetime

API_KEY  = os.environ.get("MINIMAX_API_KEY", "")
API_URL  = "https://api.minimax.chat/anthropic/v1"
MODEL    = "MiniMax-M2.7"
TIMEOUT  = 300
KB_WIKI  = Path("/home/ht/github/knowledge-base/wiki")
POSTS_DIR = Path("/home/ht/github/blog-downloader/output/1215172700/posts")
MAX_NEW  = 50   # 最多新编译篇数

# 从预存的 pending 列表加载
PENDING_FILE = Path(__file__).parent / "pending_posts.json"
if PENDING_FILE.exists():
    with open(PENDING_FILE, encoding="utf-8") as f:
        pending_data = json.load(f)
    pending = [p["filename"] for p in pending_data]
else:
    pending = []

print(f"待编译帖子: {len(pending)} 篇")

if not pending:
    print("没有新帖子需要编译")
    sys.exit(0)

pending = pending[:MAX_NEW]

SYSTEM = """你是一位顶级缠论专家，擅长将缠中说禅的交易理论体系化整理。"""

USER_TPL = """你是一个知识库条目提取专家。请从以下新浪博客帖子中提取核心内容，生成一个结构化的知识条目。

要求：
- 用中文输出，包含标题、摘要、正文（500字以上）、标签
- 标题：直接用帖子标题，不要编造
- 摘要：2-3句话概括核心观点
- 正文：保留关键技术分析、具体数字、定量描述
- 标签：3-5个相关标签，如"缠中说禅"、"股票"、"论语"、"音乐"等
- 只输出知识条目，不要多余内容

帖子内容：
---
title: {title}
---

{content}"""

def slugify(title):
    s = re.sub(r'[\]\[【】（）、，。：；！？…—\-/\\〈〉""''·、]', '', title)
    s = re.sub(r'\s+', '_', s.strip())
    return s[:80]

def front_matter(title, summary, tags, original_post):
    tags_str = ", ".join(f'"{t}"' for t in tags)
    return f"""---
title: "{title}"
summary: "{summary}"
tags: [{tags_str}]
date: "{datetime.now().strftime('%Y-%m-%d')}"
blogger: 缠中说禅
blogger_id: "1215172700"
original_post: "{original_post}"
---

"""

def extract_title(content, fname):
    meta = {}
    if content.startswith("---"):
        parts = content[3:].split("---", 1)
        if len(parts) > 1:
            for line in parts[0].splitlines():
                if ": " in line:
                    k, v = line.split(": ", 1)
                    meta[k.strip()] = v.strip().strip('"').strip("'")
    title = meta.get("title", "")
    if not title:
        body = parts[1].strip() if len(parts) > 1 else content
        for line in body.splitlines()[:10]:
            m = re.match(r'^#+\s+(.+)$', line.strip())
            if m:
                title = m.group(1).strip()
                break
    if not title:
        title = fname.replace(".md", "")
    return title, parts[1].strip() if len(parts) > 1 else content

async def compile_one(session, fname):
    fpath = POSTS_DIR / fname
    content = fpath.read_text(encoding="utf-8")
    title, body_text = extract_title(content, fname)

    prompt = USER_TPL.format(title=title, content=body_text[:3000])

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 8192,
        "extra": {"beta": {"requests_full": True, "thinking_bypass": True}}
    }

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01"
    }

    try:
        async with session.post(f"{API_URL}/messages", json=payload, headers=headers,
                               timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as resp:
            if resp.status != 200:
                err = await resp.text()
                return None, f"HTTP {resp.status}: {err[:100]}"
            data = await resp.json()
            text = ""
            for block in data.get("content", []):
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "")
                    break
            if not text.strip():
                return None, "Empty"

            out = front_matter(title, "", ["缠中说禅"], fname)
            out += text.strip() + "\n"

            slug = slugify(title)
            out_path = KB_WIKI / f"1215172700_{slug}.md"
            if out_path.exists():
                n = 1
                while (KB_WIKI / f"1215172700_{slug}_{n:04d}.md").exists():
                    n += 1
                out_path = KB_WIKI / f"1215172700_{slug}_{n:04d}.md"

            out_path.write_text(out, encoding="utf-8")
            return out_path, None
    except asyncio.TimeoutError:
        return None, "Timeout"
    except Exception as e:
        return None, str(e)

async def main():
    connector = aiohttp.TCPConnector(limit=1)
    async with aiohttp.ClientSession(connector=connector) as session:
        ok, fail = 0, 0
        for i, fname in enumerate(pending):
            title, _ = extract_title((POSTS_DIR / fname).read_text(encoding="utf-8"), fname)
            print(f"[{i+1}/{len(pending)}] {title[:40]}... ", end="", flush=True)
            path, err = await compile_one(session, fname)
            if err:
                print(f"FAIL: {err}")
                fail += 1
            else:
                print(f"OK: {path.name}")
                ok += 1
            await asyncio.sleep(3)

    print(f"\n完成: {ok} 成功, {fail} 失败")

if __name__ == "__main__":
    asyncio.run(main())
