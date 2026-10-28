#!/usr/bin/env python3
"""
从 legacy/ 的 v1 旧数据回填 output/ 的 v2 索引元数据。

背景：`--rebuild-index` 会从单篇 md 反推索引，而 v2 的 md frontmatter 只有
article_id、正文也不含时间/作者信息，导致 pub_time / author_name / timestamp
被静默清空。v1（legacy/）的 md frontmatter 保留了 published_at、author_name、
tags 等字段，是唯一可用的回填来源。

本脚本做三件事（幂等）：
  1. 把 v1 的 published_at / author_uid / author_name / tags / images 回填进
     v2 的 index.json（仅填空缺，不覆盖已有值）；
  2. 把 v1 独有的文章补进 v2：以 v2 格式写入 {article_id}.md 并加入索引；
  3. 自动备份 index.json → index.json.bak（不覆盖已有备份）。

用法：
  python tools/backfill_from_legacy.py --dry-run   # 只报告，不写文件
  python tools/backfill_from_legacy.py             # 实际执行
"""
import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

from metadata import BlogMetadata  # noqa: E402

# uid -> (legacy 正文目录, 博主名)
SPECS = {
    '1300871220': ('legacy/1300871220/posts', '徐小明'),
    '1215172700': ('legacy/1215172700/posts_backup', '缠中说禅'),
    '1285707277': ('legacy/1285707277/posts', '股市风云'),
}


def parse_list(raw):
    """解析 frontmatter 里的 [a, b, c] 形式列表"""
    if not raw:
        return []
    raw = raw.strip()
    if raw.startswith('[') and raw.endswith(']'):
        raw = raw[1:-1]
    out = []
    for item in raw.split(','):
        item = item.strip().strip('"').strip("'")
        if item:
            out.append(item)
    return out


def parse_v1_md(path):
    """解析 v1 格式 md（hash 命名 + 富 frontmatter）"""
    text = path.read_text(encoding='utf-8', errors='ignore')
    m = re.match(r'^---\n(.*?)\n---\n?', text, re.DOTALL)
    if not m:
        return None
    fm = {}
    for line in m.group(1).split('\n'):
        if ':' in line:
            k, v = line.split(':', 1)
            fm[k.strip()] = v.strip()

    article_id = fm.get('id', '')
    if not article_id:
        return None

    body = text[m.end():].lstrip('\n')

    title = ''
    for line in body.split('\n'):
        s = line.strip()
        if s and not s.startswith('#'):
            title = s[:200]
            break

    images = parse_list(fm.get('images', ''))
    try:
        images_count = int(fm.get('images_count', '') or 0)
    except ValueError:
        images_count = 0
    if not images_count:
        images_count = len(images)

    return {
        'article_id': article_id,
        'title': title,
        'pub_time': fm.get('published_at', '') or fm.get('pub_time', ''),
        'author_uid': fm.get('author_uid', ''),
        'author_name': fm.get('author_name', ''),
        'tags': parse_list(fm.get('tags', '')),
        'images_count': images_count,
        'source_url': fm.get('source_url', ''),
        'body': body,
    }


def load_legacy(rel_dir):
    """加载 legacy 目录下所有 v1 md，返回 article_id -> 记录"""
    d = ROOT / rel_dir
    out = {}
    if not d.exists():
        return out
    for md in d.glob('*.md'):
        rec = parse_v1_md(md)
        if rec and rec['article_id'] not in out:
            out[rec['article_id']] = rec
    return out


def process(uid, rel_dir, name, dry_run=False):
    print(f'\n{"="*70}\n{name} ({uid})\n{"="*70}')
    legacy = load_legacy(rel_dir)
    print(f'legacy 可解析文章: {len(legacy)}')

    out_dir = ROOT / 'output' / uid
    posts_dir = out_dir / 'posts'
    index_path = posts_dir / 'index.json'
    if not index_path.exists():
        print(f'  [跳过] 无 {index_path}')
        return None

    # ── 备份 ────────────────────────────────────────────────
    bak = posts_dir / 'index.json.bak'
    if not dry_run and not bak.exists():
        shutil.copy2(index_path, bak)
        print(f'  已备份 -> {bak.name}')

    meta = BlogMetadata(out_dir)
    meta.init_posts_index()
    before = len(meta.posts)
    print(f'v2 索引文章数: {before}')

    filled = {'pub_time': 0, 'author': 0, 'tags': 0, 'images': 0}
    existing = {p['article_id'] for p in meta.posts}

    # ── 1. 回填已有文章的缺失字段 ────────────────────────────
    for p in meta.posts:
        v1 = legacy.get(p['article_id'])
        if not v1:
            continue
        if not p.get('pub_time') and v1['pub_time']:
            p['pub_time'] = v1['pub_time']
            p['pub_date'] = v1['pub_time'][:10]
            p['timestamp'] = BlogMetadata._parse_timestamp(v1['pub_time'])
            filled['pub_time'] += 1
        if not p.get('author_name') and v1['author_name']:
            p['author_name'] = v1['author_name']
            p['author_uid'] = v1['author_uid'] or p.get('author_uid', '')
            filled['author'] += 1
        if not p.get('tags') and v1['tags']:
            p['tags'] = v1['tags']
            filled['tags'] += 1
        if not p.get('images_count') and v1['images_count']:
            p['images_count'] = v1['images_count']
            filled['images'] += 1

    # ── 2. 补入 v1 独有文章 ─────────────────────────────────
    added = []
    for aid, v1 in legacy.items():
        if aid in existing:
            continue
        added.append(aid)
        if not dry_run:
            posts_dir.mkdir(parents=True, exist_ok=True)
            (posts_dir / f'{aid}.md').write_text(
                f'---\narticle_id: {aid}\n---\n\n{v1["body"]}',
                encoding='utf-8',
            )
            meta.add_post({
                'article_id': aid,
                'title': v1['title'],
                'pub_time': v1['pub_time'],
                'author_uid': v1['author_uid'],
                'author_name': v1['author_name'],
                'tags': v1['tags'],
                'image_urls': [''] * v1['images_count'],
                'filename': f'{aid}.md',
                'source_url': v1['source_url'] or f'https://blog.sina.com.cn/s/blog_{aid}.html',
            })

    print(f'  回填 pub_time      : {filled["pub_time"]}')
    print(f'  回填 author_name   : {filled["author"]}')
    print(f'  回填 tags          : {filled["tags"]}')
    print(f'  回填 images_count  : {filled["images"]}')
    print(f'  新增 v1 独有文章   : {len(added)}  {added[:5]}{" ..." if len(added) > 5 else ""}')

    if not dry_run:
        meta._dirty = True
        meta.save()
        after = len(meta.posts)
        with_time = sum(1 for p in meta.posts if p.get('pub_time'))
        with_tags = sum(1 for p in meta.posts if p.get('tags'))
        print(f'  写入完成: {before} -> {after} 篇')
        print(f'  回填后覆盖率: pub_time {with_time}/{after} '
              f'({with_time/after*100:.1f}%), tags {with_tags}/{after} '
              f'({with_tags/after*100:.1f}%)')
        return {'uid': uid, 'before': before, 'after': after,
                'with_time': with_time, 'with_tags': with_tags, 'added': len(added)}

    print('  [dry-run] 未写入任何文件')
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true', help='只报告，不写文件')
    ap.add_argument('--uid', default=None, help='只处理指定 uid')
    args = ap.parse_args()

    results = []
    for uid, (rel, name) in SPECS.items():
        if args.uid and args.uid != uid:
            continue
        r = process(uid, rel, name, dry_run=args.dry_run)
        if r:
            results.append(r)

    if results and not args.dry_run:
        print(f'\n{"="*70}\n汇总\n{"="*70}')
        for r in results:
            print(f'  {r["uid"]}: {r["before"]} -> {r["after"]} 篇, '
                  f'pub_time {r["with_time"]} ({r["with_time"]/r["after"]*100:.1f}%), '
                  f'新增 {r["added"]} 篇')


if __name__ == '__main__':
    main()