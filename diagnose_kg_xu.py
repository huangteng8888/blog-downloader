#!/usr/bin/env python3
"""一次性诊断脚本：分析 output/1300871220 知识图谱的边为何过少。"""
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def analyze(rel):
    p = ROOT / rel
    data = json.loads(p.read_text(encoding='utf-8'))
    nodes = data.get('nodes', [])
    links = data.get('links', data.get('edges', []))
    print(f'=== {rel} ===')
    print(f'  nodes={len(nodes)}  links={len(links)}')
    print('  节点类型分布:', dict(Counter(n.get("type", "?") for n in nodes)))
    print('  边 relation 分布:', dict(Counter(l.get("relation", "?") for l in links)))
    # 有多少 post 节点存在（被边引用）
    src = {l.get('source') for l in links}
    tgt = {l.get('target') for l in links}
    print(f'  被引用的节点数: {len(src | tgt)}')

    # 对应 index.json 的 pub_time / author_name 覆盖情况
    idx = ROOT / rel.replace('knowledge_graph.json', 'posts/index.json')
    if idx.exists():
        idata = json.loads(idx.read_text(encoding='utf-8'))
        posts = idata.get('posts', [])
        with_time = sum(1 for x in posts if x.get('pub_time'))
        with_author = sum(1 for x in posts if x.get('author_name'))
        print(f'  index.json: posts={len(posts)} 有pub_time={with_time} 有author_name={with_author}')


for rel in ('output/1300871220/knowledge_graph.json',
            'output/1215172700/knowledge_graph.json'):
    analyze(rel)
    print()