#!/usr/bin/env python3
"""编译剩余未完成帖子（直接从 raw posts 目录加载）"""
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

# 读取所有已编译 wiki 标题
compiled = set()
for f in os.listdir(KB_WIKI):
    if f.startswith("1215172700_") and f.endswith(".md"):
        title = f[len("1215172700_"):-len(".md")]
        title = re.sub(r'_\d+$', '', title).replace('_', ' ')
        compiled.add(title[:60])  # 60字符截断比较

print(f"已编译: {len(compiled)} 篇")

# 收集未编译帖子
pending = []
for fname in os.listdir(POSTS_DIR):
    if not fname.endswith(".md"):
        continue
    with open(POSTS_DIR / fname, encoding="utf-8") as f:
        content = f.read()
    # 提取 front matter
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
        # 从正文第一行提取
        body = parts[1] if len(parts) > 1 else content
        for line in body.splitlines()[:10]:
            m = re.match(r'^#+\s+(.+)$', line.strip())
            if m:
                title = m.group(1).strip()
                break
    if not title:
        title = fname.replace(".md","")

    # 截断比较
    if title[:60] not in compiled:
        pending.append({"title": title, "filename": fname})

print(f"未编译: {len(pending)} 篇")
print(f"将编译: {min(len(pending), MAX_NEW)} 篇")

if not pending:
    print("没有新帖子需要编译")
    sys.exit(0)

# 限制数量
pending = pending[:MAX_NEW]

SYSTEM = """你是一位顶级缠论专家，擅长将缠中说禅的交易理论体系化整理。"""

USER_TPL = """你是一个知识库条目提取专家。请从以下新浪博客帖子中提取核心内容，生成一个结构化的知识条目。

要求：
- 用中文输出，包含标题、摘要、正文（300字以上）、标签
- 标题：直接从帖子内容提取真实标题，不要编造
- 摘要：2-3句话概括核心观点
- 正文：保留关键技术分析、具体数字、定量描述
- 标签：3-5个相关标签，如"缠中说禅"、"股票"、"论语"、"音乐"等
- 只输出知识条目，不要多余内容

帖子内容：
---
Title: {title}
---

{content}"""

def slugify(title):
    s = re.sub(r'[【】\[\]（）\(\)《》〈〉""''·、，。：；！？…—\-/\\]', '', title)
    s = re.sub(r'\s+', '_', s.strip())
    return s[:80]

def front_matter(title, summary, tags):
    tags_str = ", ".join(f'"{t}"' for t in tags)
    return f"""---
title: "{title}"
summary: "{summary}"
tags: [{tags_str}]
date: "{datetime.now().strftime('%Y-%m-%d')}"
blogger: 缠中说禅
blogger_id: "1215172700"
---

"""

async def compile_one(session, article):
    title = article["title"]
    fname = article["filename"]
    fpath = POSTS_DIR / fname

    with open(fpath, encoding="utf-8") as f:
        content = f.read()

    # 去掉 front matter
    if content.startswith("---"):
        parts = content[3:].split("---", 1)
        body = parts[1] if len(parts) > 1 else content
    else:
        body = content

    body_text = body.strip()

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
        async with session.post(f"{API_URL}/messages", json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as resp:
            if resp.status != 200:
                return None, f"HTTP {resp.status}"
            data = await resp.json()
            text = ""
            for block in data.get("content", []):
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "")
                    break
            if not text.strip():
                return None, "Empty"
            # 解析知识条目并写入
            out = front_matter(title, "", ["缠中说禅"])
            out += text.strip() + "\n"
            slug = slugify(title)
            out_path = KB_WIKI / f"1215172700_{slug}.md"
            # 如果已存在，加序号
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
    connector = aiohttp.TCPConnector(limit=3)
    async with aiohttp.ClientSession(connector=connector) as session:
        ok, fail = 0, 0
        for i, art in enumerate(pending):
            print(f"[{i+1}/{len(pending)}] {art['title'][:40]}... ", end="", flush=True)
            path, err = await compile_one(session, art)
            if err:
                print(f"FAIL: {err}")
                fail += 1
            else:
                print(f"OK: {path.name}")
                ok += 1
            await asyncio.sleep(1)  # 防限流
    print(f"\n完成: {ok} 成功, {fail} 失败")

if __name__ == "__main__":
    asyncio.run(main())
