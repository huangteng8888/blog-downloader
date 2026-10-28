#!/usr/bin/env python3
"""
项目健康检查：一次性体检 index.json 元数据覆盖率与知识图谱结构。

用途：日常巡检、改动后回归。也是 docs/项目现状.md 中各项指标的取数来源。

用法：python tools/health_check.py
"""
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UIDS = {'1300871220': '徐小明', '1215172700': '缠中说禅', '1285707277': '股市风云'}
FIELDS = ['title', 'pub_time', 'pub_date', 'timestamp', 'author_name',
          'source_url', 'tags']


def pct(a, b):
    return f'{a / b * 100:5.1f}%' if b else '  n/a'


def check_index(uid):
    p = ROOT / 'output' / uid / 'posts' / 'index.json'
    if not p.exists():
        print(f'  [缺失] {p}')
        return None
    data = json.loads(p.read_text(encoding='utf-8'))
    posts = data.get('posts', [])
    n = len(posts)
    print(f'  index.json: {n} 篇  (updated_at={str(data.get("updated_at"))[:19]})')
    for f in FIELDS:
        filled = sum(1 for x in posts if x.get(f) not in (None, '', [], {}))
        print(f'    {f:13s} {filled:6d}/{n}  {pct(filled, n)}')
    dates = sorted(x['pub_date'] for x in posts if x.get('pub_date'))
    print(f'    时间跨度      {dates[0] if dates else "-"} ~ {dates[-1] if dates else "-"}')
    bak = p.with_name('index.json.bak')
    print(f'    备份          {"有" if bak.exists() else "无"}')
    return n


def check_graph(uid):
    p = ROOT / 'output' / uid / 'knowledge_graph.json'
    if not p.exists():
        print('  [缺失] knowledge_graph.json')
        return
    data = json.loads(p.read_text(encoding='utf-8'))
    nodes = data.get('nodes', [])
    links = data.get('links', data.get('edges', []))
    types = Counter(n.get('type', '?') for n in nodes)
    rels = Counter(l.get('relation', '?') for l in links)
    print(f'  knowledge_graph.json: {p.stat().st_size/1024/1024:.2f} MB  '
          f'nodes={len(nodes)} links={len(links)}')
    print(f'    节点类型: {dict(types)}')
    print(f'    边类型  : {dict(rels)}')
    bak = p.with_name('knowledge_graph.json.bak')
    print(f'    备份    : {"有" if bak.exists() else "无"}')


def main():
    print('=' * 70)
    print('BLOG-DOWNLOADER 健康检查')
    print('=' * 70)
    for uid, name in UIDS.items():
        print(f'\n【{name} ({uid})】')
        check_index(uid)
        check_graph(uid)


if __name__ == '__main__':
    main()