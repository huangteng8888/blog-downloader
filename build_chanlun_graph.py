#!/usr/bin/env python3
"""为缠论体系文档构建双向链接图谱"""
import re
from pathlib import Path

KB_WIKI = Path("/home/ht/github/knowledge-base/wiki")

# 缠论核心概念到文件的映射
CONCEPT_FILES = {
    "缠论总纲": "chanlun_缠论总纲全貌.md",
    "走势终完美": "chanlun_走势终完美深度解读.md",
    "走势类型": "chanlun_走势类型完整定义.md",
    "中枢": "chanlun_中枢理论构成多义性.md",
    "背驰": "chanlun_背驰理论MACD辅助.md",
    "分型": "chanlun_分型理论顶分型底分型.md",
    "笔": "chanlun_笔理论划分标准画法.md",
    "线段": "chanlun_线段理论定义破坏划分.md",
    "级别": "chanlun_级别理论多级别联立.md",
    "均线": "chanlun_均线系统融合应用.md",
    "三类买卖点": "chanlun_三类买卖点原理实战.md",
    "区间套": "chanlun_区间套定位精准买卖.md",
    "108课": "chanlun_缠论108课学习指南.md",
    "定理": "chanlun_缠论定理推论汇总.md",
    "实战": "chanlun_缠论实战系统化操作.md",
}

# 每个文档的核心关键词（用于判断关联）
DOC_KEYWORDS = {
    "chanlun_缠论总纲全貌.md": ["缠论", "中枢", "走势", "级别", "买卖点", "背驰", "笔", "线段", "分型", "区间套"],
    "chanlun_走势终完美深度解读.md": ["走势终完美", "中枢", "买卖点", "完成", "终完美"],
    "chanlun_走势类型完整定义.md": ["走势类型", "盘整", "趋势", "中枢", "背驰"],
    "chanlun_中枢理论构成多义性.md": ["中枢", "中枢震荡", "中枢延伸", "中枢扩张", "中枢破坏", "级别"],
    "chanlun_背驰理论MACD辅助.md": ["背驰", "MACD", "力度", "转折", "买卖点"],
    "chanlun_分型理论顶分型底分型.md": ["分型", "顶分型", "底分型", "包含关系", "笔"],
    "chanlun_笔理论划分标准画法.md": ["笔", "分型", "线段", "划分", "标准"],
    "chanlun_线段理论定义破坏划分.md": ["线段", "笔", "破坏", "划分", "特征序列"],
    "chanlun_级别理论多级别联立.md": ["级别", "多级别", "联立", "递归", "生长"],
    "chanlun_均线系统融合应用.md": ["均线", "吻", "MACD", "辅助", "系统"],
    "chanlun_三类买卖点原理实战.md": ["三类买卖点", "第一类买点", "第二类买点", "第三类买点", "中枢"],
    "chanlun_区间套定位精准买卖.md": ["区间套", "买卖点", "精确", "递归", "背驰"],
    "chanlun_缠论108课学习指南.md": ["108课", "课程", "学习", "缠论"],
    "chanlun_缠论定理推论汇总.md": ["定理", "定律", "推论", "走势终完美"],
    "chanlun_缠论实战系统化操作.md": ["实战", "操作", "系统", "策略", "止损", "仓位"],
}

# 构建关联矩阵（共同关键词数）
def build_related_links():
    docs = list(DOC_KEYWORDS.keys())
    links = {}  # doc -> [(related_doc, score)]

    for i, doc1 in enumerate(docs):
        scores = []
        kw1 = set(DOC_KEYWORDS[doc1])
        for doc2 in docs:
            if doc1 == doc2:
                continue
            kw2 = set(DOC_KEYWORDS[doc2])
            common = kw1 & kw2
            if common:
                scores.append((doc2, len(common), common))
        scores.sort(key=lambda x: -x[1])
        links[doc1] = scores[:4]  # 保留最相关的4个

    return links

def add_see_also_links():
    links = build_related_links()

    for doc, related in links.items():
        fpath = KB_WIKI / doc
        if not fpath.exists():
            continue

        content = fpath.read_text(encoding="utf-8")

        # 找到"相关文档"或"See also"部分（在文档末尾）
        see_also_marker = "\n\n---\n\n## 相关文档\n"
        existing_marker = "\n\n## 相关文档\n"

        # 移除旧的see also
        if existing_marker in content:
            content = content.split(existing_marker)[0]

        # 构建新see also
        items = []
        for rel_doc, score, common in related:
            rel_name = rel_doc.replace("chanlun_", "").replace(".md", "")
            items.append(f"- [[{rel_doc}|{rel_name}]] （共享: {', '.join(list(common)[:3])}）")

        see_also = see_also_marker + "\n".join(items)

        # 追加到正文前或末尾
        if content.endswith("\n"):
            content += see_also
        else:
            content += "\n" + see_also

        fpath.write_text(content, encoding="utf-8")
        print(f"✓ {doc}")

    print(f"\n完成: {len(links)} 篇文档已添加关联链接")

if __name__ == "__main__":
    add_see_also_links()
