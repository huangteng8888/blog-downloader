#!/usr/bin/env python3
"""逐篇生成 3 篇缠论体系文档（串行 + 超长超时）"""
import asyncio, aiohttp, json, os, sys
from pathlib import Path
from datetime import datetime, timezone

API_KEY  = os.environ.get("MINIMAX_API_KEY", "")
API_URL  = "https://api.minimax.chat/anthropic/v1/messages"
TIMEOUT  = 600  # 10分钟超时
KB_WIKI  = Path("/home/ht/github/knowledge-base/wiki")

DOCS = [
    {
        "title": "背驰理论：背驰判断与MACD辅助用法",
        "slug": "背驰理论MACD辅助",
        "tags": ["缠论体系", "背驰"],
        "body": """请撰写一篇缠论教学文档：背驰理论：背驰判断与MACD辅助用法

写作要求：
1. 语言风格：专业、严谨、有深度，类似缠中说禅原文论述风格
2. 结构：开篇点题，分多节展开，每节有定义/原理/应用
3. 必须包含具体数字（如背驰判断的具体标准、MACD参数设置等）
4. 结尾：有小结或实践要点
5. 输出：直接markdown正文，一级标题为文章标题

写作提示：
- 背驰的本质：前后同向走势段之间的力度比较
- 判断背驰的方法：均线、MACD面积、走势结构
- 盘整背驰与趋势背驰的区别
- 背驰段的区间套定位
- MACD对背驰的辅助判断（15课、24课内容）
- 第一类买卖点与背驰的关系"""
    },
    {
        "title": "均线系统与缠论的融合应用",
        "slug": "均线系统融合应用",
        "tags": ["缠论体系", "均线"],
        "body": """请撰写一篇缠论教学文档：均线系统与缠论的融合应用

写作要求：
1. 语言风格：专业、严谨、有深度，类似缠中说禅原文论述风格
2. 结构：开篇点题，分多节展开，每节有定义/原理/应用
3. 必须包含具体数字（如均线参数的设置、均线与缠论各级别对应关系）
4. 结尾：有小结或实践要点
5. 输出：直接markdown正文，一级标题为文章标题

写作提示：
- 均线系统（MA5/10/20/60/120/250）如何辅助判断趋势
- 均线多头排列与空头排列的实战意义
- 均线与分型、笔、中枢的配合使用
- 乖离率（BIAS）对背驰的辅助判断
- 均线操作系统的构建：选时、选股、持仓、卖出"""
    },
    {
        "title": "缠论实战：从小白到系统化操作",
        "slug": "缠论实战系统化操作",
        "tags": ["缠论体系", "实战"],
        "body": """请撰写一篇缠论教学文档：缠论实战：从小白到系统化操作

写作要求：
1. 语言风格：专业、严谨、有深度，类似缠中说禅原文论述风格
2. 结构：开篇点题，分多节展开，每节有定义/原理/应用
3. 必须包含具体数字和操作标准
4. 结尾：有小结或实践要点
5. 输出：直接markdown正文，一级标题为文章标题

写作提示：
- 实战操作闭环：选时（级别判断）→选股→买入（三类买卖点）→持有（中枢延伸处理）→卖出（背驰判断）
- 如何选择操作级别（1分钟/5分钟/30分钟）
- 选股的基本面与技术面结合
- 三类买卖点的实战应用案例
- 中枢延伸、扩展、再生产的处理
- 止损原则与仓位管理
- 心态管理：为什么执行力比分析更重要"""
    },
]

async def call_llm(session, prompt, system_prompt=""):
    url = API_URL
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": "MiniMax-M2.7",
        "messages": messages,
        "max_tokens": 8192,
        "extra": {"beta": {"requests_full": True, "thinking_bypass": True}},
    }

    async with session.post(url, json=payload, headers=headers,
                           timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as resp:
        raw = await resp.text()
        if resp.status != 200:
            return "", f"HTTP {resp.status}: {raw[:200]}"
        data = json.loads(raw)
        for block in data.get("content", []):
            if isinstance(block, dict) and block.get("type") == "text":
                txt = block.get("text", "").strip()
                if txt:
                    return txt, ""
        return "", f"No text block. thinking={[b.get('thinking','')[:100] for b in data.get('content',[]) if b.get('type')=='thinking']}"

def write_wiki(title, body, slug):
    dst = KB_WIKI / f"chanlun_{slug}.md"
    fm = [
        "---",
        f"title: \"{title}\"",
        f"summary: \"{body[:200].replace(chr(10), ' ').strip()}\"",
        f"tags: [缠论体系]",
        f"created: \"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}\"",
        f"related: [缠论体系]",
        "---",
        "",
    ]
    with open(dst, "w", encoding="utf-8") as f:
        f.write("\n".join(fm))
        f.write(body)
        f.write("\n")
    return dst

async def main():
    if not API_KEY:
        print("Error: MINIMAX_API_KEY not set")
        sys.exit(1)

    connector = aiohttp.TCPConnector(limit=1)
    async with aiohttp.ClientSession(connector=connector) as session:
        for i, doc in enumerate(DOCS, 1):
            slug = doc["slug"]
            dst_path = KB_WIKI / f"chanlun_{slug}.md"
            if dst_path.exists():
                print(f"[{i}/3] 跳过（已存在）: chanlun_{slug}.md")
                continue

            print(f"[{i}/3] 生成中: {doc['title']} ...", flush=True)
            body, err = await call_llm(session, doc["body"])

            if err:
                print(f"[{i}/3] FAIL: {err}")
            else:
                dst = write_wiki(doc["title"], body, slug)
                print(f"[{i}/3] OK: {dst.name} ({len(body)} chars)")
            await asyncio.sleep(5)

    print("\n完成！")

if __name__ == "__main__":
    asyncio.run(main())
