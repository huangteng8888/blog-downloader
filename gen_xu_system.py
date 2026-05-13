#!/usr/bin/env python3
"""为徐小明生成体系化文档（类似缠论体系）"""
import asyncio, aiohttp, json, os, sys
from pathlib import Path
from datetime import datetime, timezone

API_KEY  = os.environ.get("MINIMAX_API_KEY", "")
API_URL  = "https://api.minimax.chat/anthropic/v1"
MODEL    = "MiniMax-M2.7"
API_TIMEOUT = 300
KB_WIKI  = Path("/home/ht/github/knowledge-base/wiki")

# 徐小明核心概念体系（从wiki标题提取）
CORE_CONCEPTS = [
    ("徐小明量化交易体系总纲", "总纲", """徐小明是一位资深股票技术分析师，擅长波浪理论、量化分析和精准择时。
其分析体系以"量化"为核心特征，追求可复制、可验证的交易方法。
请写一篇总纲式文章，介绍徐小明量化交易体系的整体框架。
要求：提及核心工具（120分钟线、15分钟线、序列高9等）、分析周期体系、
趋势判断标准、实战应用要点。"""),

    ("顶部结构与底部结构", "结构", """徐小明技术分析体系中最核心的概念是"顶部结构"和"底部结构"。
请写一篇系统介绍这两个概念的文档。
要求：定义什么是顶部结构/底部结构，形成条件（钝化+结构形成），
量化标准（MACD指标、乖离率等），实战中的应用（如何利用结构判断转折点）。"""),

    ("波浪理论实战应用", "波浪", """徐小明将艾略特波浪理论与中国A股实战结合，形成了独特的波浪分析方法。
请写一篇关于波浪理论实战应用的文档。
要求：5浪上升和3浪下跌的标准结构，时间对称性原则，延长浪的识别，
波浪比率（0.382、0.5、0.618回撤），各浪操作要点。"""),

    ("时间对称性与精准择时", "时间", """徐小明特别重视"时间对称性"在技术分析中的应用。
请写一篇关于时间对称性与精准择时的文档。
要求：什么是时间对称性，在波浪理论中的应用，
如何通过相邻调整浪的时间关系预判拐点，实战中如何利用序列高9/低9。"""),

    ("钝化识别与转化", "钝化", """钝化是徐小明技术分析体系的独特概念，是结构形成的前奏。
请写一篇关于钝化识别与转化的文档。
要求：顶部钝化/底部钝化的定义，钝化与结构的关系，
哪些情况下钝化会消失（如何判断钝化能否转化为结构），
钝化阶段的操作策略。"""),

    ("多周期联动分析", "周期", """徐小明分析体系强调多周期联动，从小级别（15分钟）到大级别（120分钟、日线）联立判断。
请写一篇关于多周期联动分析的文档。
要求：周期划分的原则（1分钟/5分钟/15分钟/60分钟/120分钟/日线），
大小周期矛盾时的处理原则，如何用小周期结构确认大周期趋势，
区间套定位的应用。"""),

    ("趋势判断与操作策略", "趋势", """趋势判断是所有技术分析的基础。徐小明有自己独特的趋势判断标准。
请写一篇关于徐小明趋势判断与操作策略的文档。
要求：上升趋势/下降趋势的判定标准（均线、趋势线、结构），
不同趋势下的操作原则（顺势而为、逆势抄底逃顶），
趋势转折的判断（顶部结构+顶部背离）组合信号。"""),

    ("实战案例分析范式", "实战", """徐小明分析的核心目的是指导实战操作。
请写一篇关于实战案例分析范式的文档。
要求：徐小明分析大盘和个股的标准化步骤，
典型案例的分析框架（如何识别关键点位、如何预判走势），
操作计划制定方法（买什么、买多少、何时买、何时卖）。"""),
]

SYSTEM = """你是一位顶级股票技术分析专家，精通波浪理论、量化分析和缠论。
你的风格：专业、精准、用数据说话，善于用具体数字和量化标准表达。"""

USER_TPL = """你是一位顶级股票技术分析专家。根据以下写作提示，写一篇结构完整的markdown教学文档。

写作提示：
{concept_prompt}

要求：
1. 语言风格：专业、精准、有深度，用词精确，类似徐小明分析风格
2. 结构：开篇点题，分多节展开，每节有定义/原理/应用
3. 内容：必须有具体数字和量化标准
4. 关联：适当引用波浪理论核心概念并说明关系
5. 输出：直接markdown正文，一级标题为文章标题
6. 字数：2000字以上"""

def slugify(title):
    import re
    s = re.sub(r'[\]\[【】（）、，。：；！？…—\-/\\〈〉""''·、]', '', title)
    s = re.sub(r'\s+', '_', s.strip())
    return s[:80]

def front_matter(title):
    return f"""---
title: "{title}"
summary: "徐小明量化交易体系核心文档"
tags: ["徐小明", "量化交易", "波浪理论", "技术分析"]
date: "{datetime.now().strftime('%Y-%m-%d')}"
blogger: 徐小明
blogger_id: "1300871220"
---

"""

async def generate_doc(session, title, concept_name, prompt_text):
    user_msg = USER_TPL.format(concept_prompt=prompt_text)
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
        for title, concept_name, prompt_text in CORE_CONCEPTS:
            out_path = KB_WIKI / f"1300871220_{slugify(title)}.md"
            if out_path.exists():
                print(f"[SKIP] {title} (已存在)")
                continue
            print(f"[生成中] {title}...", end="", flush=True)
            text, err = await generate_doc(session, title, concept_name, prompt_text)
            if err:
                print(f" FAIL: {err}")
            else:
                content = front_matter(title) + text + "\n"
                out_path.write_text(content, encoding="utf-8")
                print(f" OK ({len(text)} chars)")
            await asyncio.sleep(2)
    print("\n徐小明体系文档生成完成！")

if __name__ == "__main__":
    asyncio.run(main())
