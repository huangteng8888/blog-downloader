#!/usr/bin/env python3
"""
从 v1 旧图谱（Graphify 格式 JSON）流式提取 post 节点元数据，回填 v2 索引。

v1 图谱的 post 节点形如：
    {
      "type": "post",
      "title": "",
      "author": "股市风云",
      "published_at": "2009-06-21 16:59:02",
      "category": "",
      "tags": ["财经", "股票", ...],
      "id": "4ca2560d0100dw26"        ← article_id
    }

文件是 indent=2 的格式化 JSON，键各自占一行，因此可逐行流式解析，
无需把 2.15GB 一次性载入内存。

用法：
  python tools/recover_from_v1_graph.py --uid 1285707277 --dry-run
  python tools/recover_from_v1_graph.py --uid 1285707277
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

from metadata import BlogMetadata  # noqa: E402


def _val(line):
    """从 '  "key": value,' 提取 value 并反序列化"""
    raw = line.split(':', 1)[1].strip().rstrip(',')
    try:
        return json.loads(raw)
    except Exception:
        return raw.strip('"')


def stream_post_nodes(path, limit_bytes=None):
    """流式产出 v1 图谱中的 post 节点 dict"""
    cur = None
    in_tags = False
    read = 0
    with path.open('r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            read += len(line)
            if limit_bytes and read > limit_bytes:
                break
            s = line.strip()

            if s.startswith('"type": "post"'):
                cur = {}
                in_tags = False
                continue

            if cur is None:
                continue

            if in_tags:
                if s.startswith(']'):
                    in_tags = False
                else:
                    tag = s.rstrip(',').strip().strip('"').replace('\\"', '').strip()
                    if tag:
                        cur.setdefault('tags', []).append(tag)
                continue

            if s.startswith('"tags":'):
                if s.endswith('['):
                    in_tags = True
                else:
                    cur['tags'] = _val(s)
                continue

            if s.startswith('"published_at":'):
                cur['published_at'] = _val(s)
            elif s.startswith('"author":'):
                cur['author'] = _val(s)
            elif s.startswith('"author_name":'):
                cur['author'] = _val(s)
            elif s.startswith('"author_uid":'):
                cur['author_uid'] = _val(s)
            elif s.startswith('"title":'):
                cur['title'] = _val(s)
            elif s.startswith('"id":'):
                cur['id'] = _val(s)
                yield cur
                cur = None
            elif s.startswith('}') or s.startswith(']'):
                # 没有 id 节点的兜底
                if cur:
                    yield cur
                cur = None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--uid', required=True)
    ap.add_argument('--graph', default=None, help='v1 图谱路径，默认 legacy/{uid}/knowledge_graph.json')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--limit-bytes', type=int, default=None, help='仅解析前 N 字节（测试用）')
    args = ap.parse_args()

    uid = args.uid
    graph_path = Path(args.graph) if args.graph else ROOT / 'legacy' / uid / 'knowledge_graph.json'
    print(f'v1 图谱: {graph_path}')
    if not graph_path.exists():
        print('  [跳过] 文件不存在')
        return

    # ── 1. 流式提取 post 节点 ───────────────────────────────
    meta_map = {}
    n_posts = 0
    n_dup = 0
    for node in stream_post_nodes(graph_path, args.limit_bytes):
        aid = node.get('id', '')
        if not aid:
            continue
        n_posts += 1
        if aid in meta_map:
            n_dup += 1
            continue
        meta_map[aid] = node
    print(f'  提取 post 节点: {n_posts}（去重后 {len(meta_map)}，重复 {n_dup}）')
    with_time = sum(1 for v in meta_map.values() if v.get('published_at'))
    with_tags = sum(1 for v in meta_map.values() if v.get('tags'))
    print(f'  含 published_at: {with_time}   含 tags: {with_tags}')

    # ── 2. 回填 v2 索引 ─────────────────────────────────────
    out_dir = ROOT / 'output' / uid
    posts_dir = out_dir / 'posts'
    index_path = posts_dir / 'index.json'
    if not index_path.exists():
        print(f'  [跳过] 无 {index_path}')
        return

    if not args.dry_run:
        bak = posts_dir / 'index.json.bak'
        if not bak.exists():
            shutil.copy2(index_path, bak)
            print(f'  已备份 -> {bak.name}')

    meta = BlogMetadata(out_dir)
    meta.init_posts_index()
    before = len(meta.posts)

    filled = {'pub_time': 0, 'author': 0, 'tags': 0}
    for p in meta.posts:
        v1 = meta_map.get(p['article_id'])
        if not v1:
            continue
        if not p.get('pub_time') and v1.get('published_at'):
            p['pub_time'] = v1['published_at']
            p['pub_date'] = v1['published_at'][:10]
            p['timestamp'] = BlogMetadata._parse_timestamp(v1['published_at'])
            filled['pub_time'] += 1
        if not p.get('author_name') and v1.get('author'):
            p['author_name'] = v1['author']
            p['author_uid'] = v1.get('author_uid', uid)
            filled['author'] += 1
        if not p.get('tags') and v1.get('tags'):
            p['tags'] = v1['tags']
            filled['tags'] += 1

    print(f'  回填 pub_time: {filled["pub_time"]}   author: {filled["author"]}   tags: {filled["tags"]}')

    if args.dry_run:
        print('  [dry-run] 未写入')
        return

    meta._dirty = True
    meta.save()
    after = len(meta.posts)
    wt = sum(1 for p in meta.posts if p.get('pub_time'))
    wtg = sum(1 for p in meta.posts if p.get('tags'))
    print(f'  写入完成: {before} 篇；pub_time 覆盖 {wt}/{after} ({wt/after*100:.1f}%)，'
          f'tags 覆盖 {wtg}/{after} ({wtg/after*100:.1f}%)')


if __name__ == '__main__':
    main()