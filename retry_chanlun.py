#!/usr/bin/env python3
"""重试生成 3 篇失败的缠论体系文档"""
import asyncio, aiohttp, json, os, re, sys
from pathlib import Path
from datetime import datetime, timezone

API_KEY  = os.environ.get("MINIMAX_API_KEY", "")
API_URL  = "https://api.minimax.chat/anthropic/v1"
API_TIMEOUT = 300
MODEL    = "MiniMax-M2.7"
KB_WIKI  = Path("/home/ht/github/knowledge-base/wiki")

FAILED_DOCS = [
    {"id": "chanlun_009", "title": "背驰理论：背驰判断与MACD辅助用法",
     "slug": "背驰理论MACD辅助", "tags": ["缠论体系", "背驰"],
     "body_prompt": "深入讲解背驰理论：背驰的本质（前后同向走势段之间的力度比较），判断背驰的方法（均线、MACD面积、走势结构），盘整背驰与趋势背驰的区别，背驰段的区间套定位。"},
    {"id": "chanlun_014", "title": "均线系统与缠论的融合应用",
     "slug": "均线系统融合应用", "tags": ["缠论体系", "均线"],
     "body_prompt": "讲解均线系统与缠论的融合：均线（MA5/10/20/60/120/250）如何辅助判断趋势、支撑阻力、多空分界，均线与分型笔中枢的配合使用，均线操作系统的构建。"},
    {"id": "chanlun_015", "title": "缠论实战：从小白到系统化操作",
     "slug": "缠论实战系统化操作", "tags": ["缠论体系", "实战"],
     "body_prompt": "综合缠论各理论，提供完整实战操作闭环：如何选时（级别判断）、如何选股（板块与基本面）、如何买入（三类买卖点）、如何持有（中枢延伸处理）、如何卖出（背驰判断）。结合实战案例说明。"},
]

SYSTEM_PROMPT = """你是一位顶级缠论专家。根据以下写作提示，写一篇结构完整、论述严谨的markdown教学文档。

要求：
1. 语言风格：专业、严谨、有深度，用词精确，类似缠中说禅原文论述风格
2. 结构：开篇点题，分多节展开，每节有定义/原理/应用
3. 内容：必须有具体数字和缠论原文表述
4. 结尾：有小结或实践要点
5. 输出：直接markdown正文，一级标题为文章标题，二级标题为章节名"""

def build_user_prompt(doc, context=""):
    # 不附 context，完全依靠模型知识
    return f"请撰写这篇缠论教学文档：{doc['title']}\n\n写作提示：{doc['body_prompt']}"

def load_context():
    """加载前50篇缠论 wiki 的摘要"""
    wiki_dir = Path("/home/ht/github/knowledge-base/wiki")
    chunks = []
    for fp in sorted(wiki_dir.glob("1215172700_*.md"))[:50]:
        try:
            with open(fp, encoding="utf-8") as f:
                content = f.read()
            sm = re.search(r'summary: "(.*?)"', content)
            title_m = re.search(r'title: "(.*?)"', content)
            if sm:
                chunks.append(f"[{title_m.group(1) if title_m else fp.stem}]: {sm.group(1)}")
        except Exception:
            pass
    return "\n".join(chunks[:80])

def extract_text(data):
    """从 MiniMax API 响应中提取纯文本内容（text 块优先）"""
    for block in data.get("content", []):
        if isinstance(block, dict) and block.get("type") == "text":
            txt = block.get("text", "").strip()
            if txt:
                return txt
    return ""

def write_doc(doc, body):
    dst = KB_WIKI / f"chanlun_{doc['slug']}.md"
    fm = [
        "---",
        f'title: "{doc["title"]}"',
        f'summary: "{body[:200].replace(chr(10), " ").strip()}"',
        f"tags: [{', '.join(doc['tags'])}]",
        f'created: "{datetime.now(timezone.utc).strftime("%Y-%m-%d")}"',
        f"related: [{', '.join(doc['tags'])}]",
        "---", "",
    ]
    with open(dst, "w", encoding="utf-8") as f:
        f.write("\n".join(fm))
        f.write(body)
        f.write("\n")
    return dst

async def generate(session, doc, context):
    url = f"{API_URL}/messages"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(doc, context)},
        ],
        "max_tokens": 8192,
        "extra": {"beta": {"requests_full": True, "thinking_bypass": True}},
    }
    for attempt in range(2):
        try:
            async with session.post(url, json=payload, headers=headers,
                                   timeout=aiohttp.ClientTimeout(total=API_TIMEOUT)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    text = extract_text(data)
                    return text.strip() if text.strip() else "", "Empty"
                elif resp.status == 429:
                    await asyncio.sleep(15)
                    continue
                else:
                    return "", f"HTTP {resp.status}"
        except asyncio.TimeoutError:
            if attempt < 1:
                await asyncio.sleep(5)
                continue
            return "", "Timeout"
        except Exception as e:
            return "", str(e)
    return "", "Max retries"

async def main():
    if not API_KEY:
        print("Error: MINIMAX_API_KEY not set")
        sys.exit(1)

    context = load_context()
    print(f"上下文: {len(context)} chars (已忽略，改为纯模型输出)")

    async with aiohttp.ClientSession() as session:
        for doc in FAILED_DOCS:
            body, err = await generate(session, doc, "")
            if err:
                print(f"  [FAIL] {doc['title']}: {err}")
            else:
                dst = write_doc(doc, body)
                print(f"  [OK] {dst.name} ({len(body)} chars)")
            await asyncio.sleep(3)

    print("\n完成！")

if __name__ == "__main__":
    asyncio.run(main())
