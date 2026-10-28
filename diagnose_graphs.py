#!/usr/bin/env python3
"""一次性诊断脚本：校验各 knowledge_graph.json 是否结构完好。"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent

TARGETS = [
    'output/1300871220/knowledge_graph.json',
    'output/1215172700/knowledge_graph.json',
    'output/1285707277/knowledge_graph.json',
    'legacy/1300871220/knowledge_graph.json',
    'legacy/1215172700/knowledge_graph.json',
]


def check(rel):
    p = ROOT / rel
    if not p.exists():
        print(f'{rel:50s} 不存在')
        return
    size = p.stat().st_size
    head = p.read_text(encoding='utf-8', errors='ignore')[:1]
    try:
        data = json.loads(p.read_text(encoding='utf-8'))
    except Exception as e:
        print(f'{rel:50s} {size/1024/1024:8.2f} MB  解析失败: {type(e).__name__}')
        return
    nodes = len(data.get('nodes', []))
    links = len(data.get('links', data.get('edges', [])))
    empty_id = sum(1 for n in data.get('nodes', []) if not n.get('id'))
    verdict = 'OK' if nodes > 0 else '空图(损坏)'
    print(f'{rel:50s} {size/1024/1024:8.2f} MB  nodes={nodes:<6} links={links:<6} '
          f'空id={empty_id:<4} {verdict}')


if __name__ == '__main__':
    print('注意：legacy/1285707277/knowledge_graph.json 体积 2.15GB，跳过解析')
    for t in TARGETS:
        check(t)