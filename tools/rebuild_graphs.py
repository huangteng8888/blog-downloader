#!/usr/bin/env python3
"""
重建三个博主的知识图谱（用回填后的 index.json）。

背景：
  - `load_from_index()` 此前未调用 `_add_tag_edges()`，且 v2 索引丢弃了 tags，
    导致标签关系为空；
  - 徐小明的图谱因元数据被清空，只剩 171 条边（应约 2.5 万条）。

本工具会先把现有图谱备份为 knowledge_graph.json.bak，再重建。

用法：
  python tools/rebuild_graphs.py --dry-run
  python tools/rebuild_graphs.py
  python tools/rebuild_graphs.py --with-keywords   # 额外抽取关键词节点（较慢）
"""
import argparse
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

from blog_graph import BlogGraphBuilder  # noqa: E402

UIDS = ['1300871220', '1215172700', '1285707277', '1216826604']
NAMES = {'1300871220': '徐小明', '1215172700': '缠中说禅',
         '1285707277': '股市风云', '1216826604': 'wu2198'}


def load_contents(posts_dir):
    """读取 md 正文，用于关键词抽取（仅 --with-keywords 时调用）"""
    import re
    out = {}
    for md in posts_dir.glob('*.md'):
        text = md.read_text(encoding='utf-8', errors='ignore')
        text = re.sub(r'^---\n.*?\n---\n?', '', text, flags=re.DOTALL)
        out[md.stem] = text
    return out


def rebuild(uid, with_keywords=False, dry_run=False):
    print(f'\n{"="*66}\n{NAMES[uid]} ({uid})\n{"="*66}')
    out_dir = ROOT / 'output' / uid
    posts_dir = out_dir / 'posts'
    index_path = posts_dir / 'index.json'
    if not index_path.exists():
        print('  [跳过] 无 index.json')
        return None

    builder = BlogGraphBuilder()
    if with_keywords:
        builder.load_from_index(index_path)
        contents = load_contents(posts_dir)
        added = 0
        for aid, content in contents.items():
            if aid in builder.graph:
                for kw in builder._extract_keywords(content):
                    if kw not in builder.graph:
                        builder.graph.add_node(kw, type='keyword')
                    builder.graph.add_edge(aid, kw,
                                           relation='contains_keyword',
                                           confidence='EXTRACTED')
                    added += 1
        print(f'  关键词边: {added}')
    else:
        builder.load_from_index(index_path)

    g = builder.graph
    rel = Counter(d.get('relation', '?') for _, _, d in g.edges(data=True))
    types = Counter(d.get('type', '?') for _, d in g.nodes(data=True))
    print(f'  nodes={g.number_of_nodes()}  links={g.number_of_edges()}')
    print(f'  节点类型: {dict(types)}')
    print(f'  边类型  : {dict(rel)}')

    old = out_dir / 'knowledge_graph.json'
    if old.exists():
        old_size = old.stat().st_size / 1024 / 1024
        print(f'  旧图谱: {old_size:.2f} MB')

    if dry_run:
        print('  [dry-run] 未写入')
        return None

    bak = out_dir / 'knowledge_graph.json.bak'
    if old.exists() and not bak.exists():
        shutil.move(str(old), str(bak))
        print('  已备份旧图谱 -> knowledge_graph.json.bak')

    builder.export_json(old)
    print(f'  已写入: {old.stat().st_size/1024/1024:.2f} MB')
    return {'uid': uid, 'nodes': g.number_of_nodes(), 'links': g.number_of_edges(),
            'relations': dict(rel)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--with-keywords', action='store_true')
    ap.add_argument('--uid', default=None)
    args = ap.parse_args()

    results = []
    for uid in UIDS:
        if args.uid and args.uid != uid:
            continue
        r = rebuild(uid, args.with_keywords, args.dry_run)
        if r:
            results.append(r)

    if results:
        print(f'\n{"="*66}\n汇总\n{"="*66}')
        for r in results:
            print(f'  {NAMES[r["uid"]]}: nodes={r["nodes"]} links={r["links"]} {r["relations"]}')


if __name__ == '__main__':
    main()