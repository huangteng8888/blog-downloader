#!/usr/bin/env python3
"""为股市风云生成体系化文档"""
import asyncio, aiohttp, os
from pathlib import Path
from datetime import datetime

API_KEY  = os.environ.get("MINIMAX_API_KEY", "")
API_URL  = "https://api.minimax.chat/anthropic/v1"
MODEL    = "MiniMax-M2.7"
API_TIMEOUT = 300
KB_WIKI  = Path("/home/ht/github/knowledge-base/wiki")

# 股市风云核心主题（从wiki标题提取的关键分析维度）
CORE_TOPICS = [
    ("股市风云技术分析总纲", "总纲", """股市风云是一位资深股票市场评论员，其分析以实战为导向，
结合大盘点位判断、板块轮动分析和政策解读。
请写一篇总纲式文章，介绍股市风云的实战分析体系整体框架。
要求：涵盖大盘点位判断、板块分析、资金流向、政策解读等维度。"""),

    ("大盘点位判断与关键位置分析", "点位", """股市风云分析中一个显著特点是精准的大盘点位预判。
请写一篇关于大盘点位判断与关键位置分析的文档。
要求：如何确定关键点位（压力位/支撑位），历史高点/低点的参考价值，
均线系统（如30日均线、60日均线、120日均线）的应用，
突破与回踩的判断标准。"""),

    ("板块轮动与热点切换", "板块", """股市风云特别重视板块轮动和热点切换的分析。
请写一篇关于板块轮动与热点切换的文档。
要求：热点板块的形成逻辑，板块轮动的规律（如金融→消费→科技），
政策导向与热点板块的关系，如何捕捉龙头板块和龙头股。"""),

    ("量价关系分析", "量价", """成交量是判断市场走势的核心指标之一。
请写一篇关于量价关系分析的文档。
要求：放量上涨/缩量上涨的含义，地量见底的判断标准，
量价背离的技术含义，庄家吸筹与出货的量能特征。"""),

    ("政策解读与市场联动", "政策", """中国股市受政策影响显著。
请写一篇关于政策解读与市场联动的文档。
要求：政策对大盘走势的影响机制，重大政策（如加息、调控）的市场反应，
政策底与市场底的关系，如何从政策信号预判市场走势。"""),

    ("资金管理与仓位控制", "仓位", """实战操作中资金管理至关重要。
请写一篇关于资金管理与仓位控制的文档。
要求：仓位控制的基本原则（不满仓、不空仓），不同点位下的仓位策略，
加仓/减仓的标准，止损与止盈的设置方法。"""),
]

SYSTEM = """你是一位资深中国A股市场分析师，擅长结合大盘点位、板块轮动和政策面进行综合分析。"""

USER_TPL = """你是一位资深中国A股市场分析师。根据以下写作提示，写一篇结构完整的markdown教学文档。

写作提示：
{topic_prompt}

要求：
1. 语言风格：专业、实战导向、善于用具体大盘点位和案例说明
2. 结构：开篇点题，分多节展开，每节有定义/原理/实战案例
3. 内容：结合中国A股实际走势，有具体点位和板块案例
4. 结尾：有实践要点总结
5. 输出：直接markdown正文，一级标题为文章标题
6. 字数：1500字以上"""

def slugify(title):
    import re
    s = re.sub(r'[\]\[【】（）、，。：；！？…—\-/\\〈〉""''·、]', '', title)
    s = re.sub(r'\s+', '_', s.strip())
    return s[:80]

def front_matter(title):
    return f"""---
title: "{title}"
summary: "股市风云实战分析体系核心文档"
tags: ["股市风云", "技术分析", "大盘分析", "板块轮动"]
date: "{datetime.now().strftime('%Y-%m-%d')}"
blogger: 股市风云
blogger_id: "1285707277"
---

"""

async def generate_doc(session, title, prompt_text):
    user_msg = USER_TPL.format(topic_prompt=prompt_text)
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": user_msg}
        ],
        "max_tokens": 8192,
        "extra": {"beta": {"requests_full": True, "thinking_bypass": True}}
    }
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01"
    }
    async with session.post(f"{API_URL}/messages", json=payload, headers=headers,
                            timeout=aiohttp.ClientTimeout(total=API_TIMEOUT)) as resp:
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
        return text.strip(), None

async def main():
    connector = aiohttp.TCPConnector(limit=2)
    async with aiohttp.ClientSession(connector=connector) as session:
        for title, topic_name, prompt_text in CORE_TOPICS:
            out_path = KB_WIKI / f"1285707277_{slugify(title)}.md"
            if out_path.exists():
                print(f"[SKIP] {title} (已存在)")
                continue
            print(f"[生成中] {title}...", end="", flush=True)
            text, err = await generate_doc(session, title, prompt_text)
            if err:
                print(f" FAIL: {err}")
            else:
                content = front_matter(title) + text + "\n"
                out_path.write_text(content, encoding="utf-8")
                print(f" OK ({len(text)} chars)")
            await asyncio.sleep(2)
    print("\n股市风云体系文档生成完成！")

if __name__ == "__main__":
    asyncio.run(main())
