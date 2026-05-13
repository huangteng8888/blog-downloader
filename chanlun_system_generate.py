#!/usr/bin/env python3
"""
缠论体系文档生成器
基于现有 870 条缠中说禅 wiki，生成 15 篇体系化文档
"""
import asyncio, aiohttp, json, os, re, sys
from pathlib import Path
from datetime import datetime, timezone

# ---------- 配置 ----------
API_KEY     = os.environ.get("MINIMAX_API_KEY", "")
API_URL     = "https://api.minimax.chat/anthropic/v1"
API_TIMEOUT = 180
MODEL       = "MiniMax-M2.7"

WIKI_DIR    = Path("/home/ht/github/knowledge-base/wiki")
KB_WIKI     = WIKI_DIR  # 直接写入 wiki 目录

# ---------- 缠论体系文档结构 ----------
CHANLUN_DOCS = [
    {"id": "chanlun_001", "title": "缠论总纲：缠中说禅交易体系全貌",
     "slug": "缠论总纲全貌", "tags": ["缠论体系", "总纲"],
     "body_prompt": "综合缠论核心理论写一份完整总纲。包括：缠论三大基础（分型、笔、线段），中枢与走势类型，三类买卖点，级别理论，以及它们之间的逻辑关系。穿插具体数字说明。"},
    {"id": "chanlun_002", "title": "分型理论：顶分型与底分型的判断标准",
     "slug": "分型理论顶分型底分型", "tags": ["缠论体系", "分型"],
     "body_prompt": "详细阐述分型理论：顶分型与底分型的定义、识别方法，包含关系的处理，分型区间划分，以及在实战中如何通过分型判断转折点。结合MACD辅助验证。"},
    {"id": "chanlun_003", "title": "笔理论：笔的划分标准与画法规则",
     "slug": "笔理论划分标准画法", "tags": ["缠论体系", "笔"],
     "body_prompt": "系统讲解笔理论：笔的形成条件（至少5根不包含的K线），笔的划分步骤，相邻笔的关系，笔的破坏与延伸。附笔实战分析要点。"},
    {"id": "chanlun_004", "title": "线段理论：线段的定义、破坏与精细划分",
     "slug": "线段理论定义破坏划分", "tags": ["缠论体系", "线段"],
     "body_prompt": "系统讲解线段理论：线段由至少三笔构成，线段破坏的两种情况，特征序列与包含关系处理，线段划分的精细方法。"},
    {"id": "chanlun_005", "title": "中枢理论：中枢的定义、构成与多义性",
     "slug": "中枢理论构成多义性", "tags": ["缠论体系", "中枢"],
     "body_prompt": "深入讲解中枢理论：中枢由前三个连续次级别走势类型的重叠部分确定，中枢的延伸、扩展、破坏，三类买卖点与中枢的关系。配实例说明。"},
    {"id": "chanlun_006", "title": "走势类型：上涨、下跌与盘整的完整定义",
     "slug": "走势类型完整定义", "tags": ["缠论体系", "走势类型"],
     "body_prompt": "详细定义三种走势类型：上涨（高低点依次抬高）、下跌（高低点依次降低）、盘整（存在重合区间）。结合中枢说明走势的生长与终结，走势必完美的含义。"},
    {"id": "chanlun_007", "title": "三类买卖点：理论原理与实战应用",
     "slug": "三类买卖点原理实战", "tags": ["缠论体系", "买卖点"],
     "body_prompt": "系统讲解三类买卖点：第一类（走势背驰转折点）、第二类（中枢震荡高低点）、第三类（中枢突破后的次级别回抽不重新进入）。说明三类买卖点的逻辑关系与实战选择。"},
    {"id": "chanlun_008", "title": "级别理论：多级别联立分析与递归体系",
     "slug": "级别理论多级别联立", "tags": ["缠论体系", "级别"],
     "body_prompt": "讲解级别理论：级别的定义（1分钟/5分钟/30分钟/60分钟/日线/周线/月线/年线），级别递归的原则，不同级别之间的配合与切换，如何通过多级别联立找到高概率操作机会。"},
    {"id": "chanlun_009", "title": "背驰理论：背驰判断与MACD辅助用法",
     "slug": "背驰理论MACD辅助", "tags": ["缠论体系", "背驰"],
     "body_prompt": "深入讲解背驰理论：背驰的本质（前后同向走势段之间的力度比较），判断背驰的方法（均线、MACD面积、走势结构），盘整背驰与趋势背驰的区别，背驰段的区间套定位。"},
    {"id": "chanlun_010", "title": "区间套定位：精准找到买卖点的方法",
     "slug": "区间套定位精准买卖", "tags": ["缠论体系", "区间套"],
     "body_prompt": "系统讲解区间套定位：从大级别到小级别的逐步精确方法，区间的包含关系与处理，如何在背驰段中通过次级别走势找到最精确的转折点。强调精确打击的实战意义。"},
    {"id": "chanlun_011", "title": "走势终完美：缠论核心原则的深度解读",
     "slug": "走势终完美深度解读", "tags": ["缠论体系", "走势终完美"],
     "body_prompt": "解读缠论核心原则走势终完美：任何走势类型必然完成（走出来才能确认），中枢完成后必然出第三类买卖点，走势的多义性与最终必然完成性，以及这一原则在操作中的指导意义。"},
    {"id": "chanlun_012", "title": "缠论108课指南：学习路径与课程地图",
     "slug": "缠论108课学习指南", "tags": ["缠论体系", "108课"],
     "body_prompt": "基于缠中说禅教你炒股票系列文章，梳理108课的体系结构：第1-20课打基础（分型、笔、线段），第21-60课进阶（中枢、走势、买卖点），第61-108课综合实战。推荐学习顺序与重点课节。"},
    {"id": "chanlun_013", "title": "缠论定理与推论汇总",
     "slug": "缠论定理推论汇总", "tags": ["缠论体系", "定理"],
     "body_prompt": "系统汇总缠论所有核心定理与推论：走势必完美定理、中枢定理、三类买卖点定理、级别定理、背驰定理、区间套定理。以条目形式列出，每条附简要说明与应用场景。"},
    {"id": "chanlun_014", "title": "均线系统与缠论的融合应用",
     "slug": "均线系统融合应用", "tags": ["缠论体系", "均线"],
     "body_prompt": "讲解均线系统与缠论的融合：均线（MA5/10/20/60/120/250）如何辅助判断趋势、支撑阻力、多空分界，均线与分型笔中枢的配合使用，均线操作系统的构建。"},
    {"id": "chanlun_015", "title": "缠论实战：从小白到系统化操作",
     "slug": "缠论实战系统化操作", "tags": ["缠论体系", "实战"],
     "body_prompt": "综合缠论各理论，提供完整实战操作闭环：如何选时（级别判断）、如何选股（板块与基本面）、如何买入（三类买卖点）、如何持有（中枢延伸处理）、如何卖出（背驰判断）。结合实战案例说明。"},
]

# ---------- 加载缠论 wiki 上下文 ----------
def load_chanlun_context(keyword: str, max_chars: int = 3000) -> str:
    """根据关键词从缠论 wiki 中提取相关上下文"""
    if not keyword:
        return ""
    keyword_file = WIKI_DIR / "chanlun_keywords.json"
    if keyword_file.exists():
        with open(keyword_file) as f:
            kw_map = json.load(f)
        keywords = kw_map.get(keyword, [])
    else:
        # 从文件名匹配
        keywords = [keyword]
    
    chunks = []
    for kw in keywords:
        files = list(WIKI_DIR.glob(f"1215172700*{kw}*.md"))
        for fp in files[:2]:  # 每个关键词最多2篇
            try:
                content = fp.read_text(encoding="utf-8")
                # 去掉 front matter
                content = re.sub(r'^---\n.*?\n---\n', '', content, flags=re.DOTALL)
                if len(content) > max_chars:
                    content = content[:max_chars]
                chunks.append(f"【{fp.stem}】\n{content}\n")
            except Exception:
                pass
            if sum(len(c) for c in chunks) > max_chars * 3:
                break
        if sum(len(c) for c in chunks) > max_chars * 3:
            break
    
    return "\n---\n".join(chunks) if chunks else ""


def load_full_context(max_chars: int = 8000) -> str:
    """加载全部缠论 wiki 摘要作为上下文"""
    files = sorted(WIKI_DIR.glob("1215172700_*.md"))
    chunks = []
    for fp in files[:50]:  # 取前50篇作为代表性样本
        try:
            with open(fp, encoding="utf-8") as f:
                content = f.read()
            # 提取 front matter 的 summary
            sm = re.search(r'summary: "(.*?)"', content)
            title = re.search(r'title: "(.*?)"', content)
            if sm:
                chunks.append(f"[{title.group(1) if title else fp.stem}]: {sm.group(1)}")
        except Exception:
            pass
    return "\n".join(chunks[:80])  # 约 80 个摘要


# ---------- 构建 prompt ----------
SYSTEM_PROMPT = """你是一位顶级缠论专家，擅长将缠中说禅的交易理论体系化地整理成教学文档。

你的任务：根据用户提供的文档主题提示，结合缠论知识，写一篇结构完整、论述严谨的教学文档。

写作要求：
1. 语言风格：专业、严谨、有深度，用词精确，类似缠中说禅原文的论述风格
2. 结构：开篇点题，分多节深入展开，每节有定义/原理/应用
3. 内容：必须有具体数字（如笔至少5根K线、线段至少3笔、中枢由三个连续次级别走势重叠部分确定等）
4. 关联：适当引用缠论核心概念并说明它们之间的关系
5. 结尾：有小结或实践要点

输出格式：直接输出 markdown 文档正文，不需要任何包裹标记。
文章标题使用一级标题（# 标题），各章节使用二级标题（## 章节名），重要定义用加粗。
"""

def build_user_prompt(doc: dict, context: str) -> str:
    title = doc["title"]
    prompt = doc["body_prompt"]
    return f"""请撰写一篇文档：**{title}**

写作提示：{prompt}

{"以下是从缠中说禅博客整理的相关内容摘要（仅作参考，不要照抄）:\n" + context if context else "请完全依靠你对缠论理论体系的掌握来撰写这篇文档。"}"""


# ---------- 提取 LLM 回复的 markdown ----------
def extract_text_from_response(data: dict) -> str:
    """从 MiniMax API 响应中提取文本"""
    parts = []
    for block in data.get("content", []):
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts)


# ---------- 生成单篇文档 ----------
async def generate_doc(session: aiohttp.ClientSession, doc: dict, context: str) -> tuple[str, str]:
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
        "max_tokens": 16384,
        "extra": {"beta": {"requests_full": True, "thinking_bypass": True}},
    }

    for attempt in range(2):
        try:
            async with session.post(url, json=payload, headers=headers,
                                   timeout=aiohttp.ClientTimeout(total=API_TIMEOUT)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    text = extract_text_from_response(data)
                    if text.strip():
                        return text.strip(), ""
                    return "", "Empty response"
                elif resp.status == 429:
                    await asyncio.sleep(10 * (attempt + 1))
                    continue
                else:
                    return "", f"HTTP {resp.status}"
        except asyncio.TimeoutError:
            if attempt < 1:
                await asyncio.sleep(5)
                continue
            return "", f"Timeout after {API_TIMEOUT}s"
        except Exception as e:
            return "", str(e)
    return "", "Max retries exceeded"


def write_doc(doc: dict, body: str, slug: str) -> Path:
    """写入缠论体系文档，文件名格式: chanlun_{slug}.md"""
    dst = KB_WIKI / f"chanlun_{slug}.md"
    if dst.exists():
        n = 2
        while (KB_WIKI / f"chanlun_{slug}_{n}.md").exists():
            n += 1
        dst = KB_WIKI / f"chanlun_{slug}_{n}.md"

    front_matter = [
        "---",
        f'title: "{doc["title"]}"',
        f'summary: "{body[:200].replace(chr(10), " ").strip()}"',
        f"tags: [{', '.join(doc['tags'])}]",
        f'created: "{datetime.now(timezone.utc).strftime("%Y-%m-%d")}"',
        f"related: [{', '.join(doc['tags'])}]",
        "---",
        "",
    ]

    with open(dst, "w", encoding="utf-8") as f:
        f.write("\n".join(front_matter))
        f.write(body)
        f.write("\n")

    return dst


# ---------- 主循环（并发生成）----------
CONCURRENCY = 3  # 同时生成 N 篇

async def generate_one(session, doc, context):
    """生成单篇缠论文档，返回 (doc, body_or_none, err_or_none)"""
    # 检查是否已存在
    dst_path = KB_WIKI / f"chanlun_{doc['slug']}.md"
    if dst_path.exists():
        return doc, None, None, "skipped (exists)"
    body, err = await generate_doc(session, doc, context)
    return doc, body, err, None

async def main():
    if not API_KEY:
        print("Error: MINIMAX_API_KEY not set")
        sys.exit(1)

    print(f"加载缠论 wiki 上下文...")
    context = load_full_context()
    print(f"上下文长度: {len(context)} chars")

    # 过滤已存在的文档
    remaining = [d for d in CHANLUN_DOCS
                 if not (KB_WIKI / f"chanlun_{d['slug']}.md").exists()]
    done = len(CHANLUN_DOCS) - len(remaining)
    print(f"已有 {done} 篇，还需生成 {len(remaining)} 篇")

    if not remaining:
        print("全部完成！")
        return

    connector = aiohttp.TCPConnector(limit=CONCURRENCY)
    async with aiohttp.ClientSession(connector=connector) as session:
        for i in range(0, len(remaining), CONCURRENCY):
            batch = remaining[i:i+CONCURRENCY]
            print(f"\n[{i+1}-{i+len(batch)}/{len(remaining)}] 并发生成 {len(batch)} 篇")
            tasks = [generate_one(session, doc, context) for doc in batch]
            results = await asyncio.gather(*tasks)
            for doc, body, err, note in results:
                if note == "skipped (exists)":
                    print(f"  [SKIP] {doc['title']}")
                elif err:
                    print(f"  [FAIL] {doc['title']}: {err}")
                else:
                    dst = write_doc(doc, body, doc["slug"])
                    print(f"  [OK] {dst.name} ({len(body)} chars)")
            await asyncio.sleep(3)

    print(f"\n完成！缠论体系文档目录: {KB_WIKI}")


if __name__ == "__main__":
    asyncio.run(main())
