#!/usr/bin/env python3
"""一次性诊断脚本：检查各 posts/index.json 的元数据字段覆盖率。

若覆盖率极低，说明 index.json 曾被 `--rebuild-index` 重建，
而 v2 的单篇 md frontmatter 只有 article_id，重建会静默丢失全部真实元数据。
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FIELDS = ['title', 'pub_time', 'pub_date', 'timestamp', 'author_uid',
          'author_name', 'source_url', 'filename', 'images_count']


def report(uid):
    p = ROOT / 'output' / uid / 'posts' / 'index.json'
    if not p.exists():
        print(f'{uid}: 缺 index.json')
        return
    data = json.loads(p.read_text(encoding='utf-8'))
    posts = data.get('posts', [])
    n = len(posts)
    print(f'--- {uid} : {n} 篇  (updated_at={data.get("updated_at", "?")[:19]}) ---')
    for f in FIELDS:
        filled = sum(1 for x in posts if x.get(f) not in (None, '', []))
        bar = '█' * int(filled / n * 20) if n else ''
        print(f'  {f:14s} {filled:6d}/{n} ({filled/n*100:5.1f}%) {bar}')
    print()


for uid in ('1300871220', '1215172700', '1285707277'):
    report(uid)