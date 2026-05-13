#!/usr/bin/env python3
"""为缠中说禅博客文档添加缠论体系链接"""
import os, re
from pathlib import Path

KB_WIKI = Path("/home/ht/github/knowledge-base/wiki")

# 缠论概念 → 体系文档映射
CONCEPT_MAP = [
    (["走势终完美", "走势必完美"], "chanlun_走势终完美深度解读.md"),
    (["走势类型", "盘整", "上涨趋势", "下跌趋势"], "chanlun_走势类型完整定义.md"),
    (["中枢震荡", "中枢延伸", "中枢扩张", "中枢破坏", "中枢理论"], "chanlun_中枢理论构成多义性.md"),
    (["背驰", "MACD辅助", "力度判断"], "chanlun_背驰理论MACD辅助.md"),
    (["顶分型", "底分型", "分型理论", "包含关系"], "chanlun_分型理论顶分型底分型.md"),
    (["笔划分", "画笔", "笔理论"], "chanlun_笔理论划分标准画法.md"),
    (["线段划分", "线段破坏", "特征序列"], "chanlun_线段理论定义破坏划分.md"),
    (["级别", "多级别", "递归", "生长"], "chanlun_级别理论多级别联立.md"),
    (["均线", "吻系统", "飞吻", "唇吻", "湿吻"], "chanlun_均线系统融合应用.md"),
    (["第一类买点", "第二类买点", "第三类买点", "买卖点"], "chanlun_三类买卖点原理实战.md"),
    (["区间套", "精确定位"], "chanlun_区间套定位精准买卖.md"),
    (["108课", "教你炒股票"], "chanlun_缠论108课学习指南.md"),
    (["定理", "定律", "推论"], "chanlun_缠论定理推论汇总.md"),
    (["实战", "操作", "策略", "止损", "仓位"], "chanlun_缠论实战系统化操作.md"),
]

added = 0
checked = 0

for fname in os.listdir(KB_WIKI):
    if not (fname.startswith("1215172700_") and fname.endswith(".md")):
        continue

    fpath = KB_WIKI / fname
    content = fpath.read_text(encoding="utf-8")
    checked += 1

    # 检查是否已有相关体系链接
    if "## 相关缠论体系" in content or "相关缠论体系" in content:
        continue

    # 查找匹配的概念
    found = []
    for concepts, doc in CONCEPT_MAP:
        for concept in concepts:
            if concept in content:
                found.append((concept, doc))
                break

    if not found:
        continue

    # 去重
    seen = set()
    unique = []
    for concept, doc in found:
        if doc not in seen:
            seen.add(doc)
            unique.append((concept, doc))

    # 构建链接
    links = []
    for concept, doc in unique[:5]:  # 最多5个
        name = doc.replace("chanlun_", "").replace(".md", "")
        links.append(f"- [[{doc}|{name}]]（触发词：{concept}）")

    see_also = "\n\n---\n\n## 相关缠论体系\n" + "\n".join(links)

    if content.endswith("\n"):
        content += see_also
    else:
        content += "\n" + see_also

    fpath.write_text(content, encoding="utf-8")
    added += 1

print(f"检查: {checked} 篇")
print(f"添加链接: {added} 篇")
