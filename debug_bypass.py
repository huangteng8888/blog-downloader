#!/usr/bin/env python3
import asyncio, aiohttp, json, os

API_KEY = os.environ.get("MINIMAX_API_KEY", "")
API_URL = "https://api.minimax.chat/anthropic/v1"

async def test():
    url = f"{API_URL}/messages"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    
    system = """你是一位顶级缠论专家。根据以下写作提示，写一篇结构完整、论述严谨的markdown教学文档。

要求：
1. 语言风格：专业、严谨、有深度，用词精确，类似缠中说禅原文论述风格
2. 结构：开篇点题，分多节展开，每节有定义/原理/应用
3. 内容：必须有具体数字和缠论原文表述
4. 结尾：有小结或实践要点
5. 输出：直接markdown正文，一级标题为文章标题，二级标题为章节名"""

    user = """请撰写这篇缠论教学文档：背驰理论：背驰判断与MACD辅助用法

写作提示：深入讲解背驰理论：背驰的本质（前后同向走势段之间的力度比较），判断背驰的方法（均线、MACD面积、走势结构），盘整背驰与趋势背驰的区别，背驰段的区间套定位。"""

    payload = {
        "model": "MiniMax-M2.7",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": 8192,
        "extra": {"beta": {"requests_full": True, "thinking_bypass": True}},
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers,
                               timeout=aiohttp.ClientTimeout(total=300)) as resp:
            print(f"Status: {resp.status}")
            data = await resp.json()
            print(f"Keys in response: {list(data.keys())}")
            blocks = data.get("content", [])
            print(f"Content blocks ({len(blocks)}):")
            for b in blocks:
                t = b.get("type")
                txt = b.get("text", "")
                think = b.get("thinking", "")
                print(f"  [{t}] text_len={len(txt)} think_len={len(think)}")
                if txt:
                    print(f"    TEXT: {txt[:200]}")
                if think:
                    print(f"    THINK: {think[:200]}")

asyncio.run(test())
