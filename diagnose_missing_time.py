#!/usr/bin/env python3
"""精确核查：每个来源能为 251 篇缺失文章补上【非空】日期各多少篇。"""
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
UIDS = ['1300871220', '1215172700', '1285707277']


def missing(uid):
    p = ROOT / 'output' / uid / 'posts' / 'index.json'
    data = json.loads(p.read_text(encoding='utf-8'))
    return [x for x in data.get('posts', []) if not x.get('pub_time')]


def url_hash(aid):
    return hashlib.md5(f'https://blog.sina.com.cn/s/blog_{aid}.html'.encode()).hexdigest()


print('=' * 72)
print('checkpoint 文件内容')
print('=' * 72)
for uid in UIDS:
    p = ROOT / 'output' / uid / 'posts' / f'.checkpoint_{uid}.json'
    if p.exists():
        print(f'  {uid}: {p.read_text(encoding="utf-8", errors="ignore").strip()[:200]}')

miss_map = {uid: missing(uid) for uid in UIDS}

print()
print('=' * 72)
print('各来源可补【非空日期】篇数')
print('=' * 72)

for uid in UIDS:
    miss = miss_map[uid]
    ids = {m['article_id'] for m in miss}
    print(f'\n【{uid}】缺失 {len(miss)} 篇')

    # 来源 A：analysis_results.json（要求 _published_at 非空）
    p = ROOT / 'legacy' / uid / 'blog-graph' / 'analysis_results.json'
    a_hit = set()
    if p.exists():
        data = json.loads(p.read_text(encoding='utf-8', errors='ignore'))
        recs = data if isinstance(data, list) else data.get('results', [])
        by_hash = {
            (x.get('_source_file') or '').replace('.md', ''): x
            for x in recs if isinstance(x, dict)
        }
        for aid in ids:
            x = by_hash.get(url_hash(aid))
            if x and x.get('_published_at'):
                a_hit.add(aid)
    print(f'  A. analysis_results(_published_at 非空) : {len(a_hit)}')

    # 来源 B：legacy md 正文中的日期
    b_hit = set()
    sub = {'1300871220': 'posts', '1215172700': 'posts_backup'}.get(uid)
    if sub:
        d = ROOT / 'legacy' / uid / sub
        if d.exists():
            for md in d.glob('*.md'):
                t = md.read_text(encoding='utf-8', errors='ignore')
                mid = re.search(r'^id:\s*(\S+)', t, re.MULTILINE)
                if mid and mid.group(1) in ids:
                    if re.search(r'\d{4}-\d{2}-\d{2}', t):
                        b_hit.add(mid.group(1))
    print(f'  B. legacy md 正文含日期                  : {len(b_hit)}')

    # 来源 C：旧打分产物的 date
    c_hit = set()
    p = ROOT / 'keyword_discovery' / 'scored_backup_before_fix' / f'{uid}_ranked.json'
    if p.exists():
        data = json.loads(p.read_text(encoding='utf-8', errors='ignore'))
        for r in data:
            aid = r.get('article_id') or (r.get('filename') or '').replace('.md', '')
            if aid in ids and r.get('date'):
                c_hit.add(aid)
    print(f'  C. 旧打分产物 date                       : {len(c_hit)}')

    # 来源 D：v1 索引
    d_hit = set()
    p = ROOT / 'legacy' / uid / 'posts' / 'index.json'
    if p.exists():
        data = json.loads(p.read_text(encoding='utf-8', errors='ignore'))
        posts = data.get('posts', []) if isinstance(data, dict) else data
        for x in posts:
            aid = x.get('id') or x.get('article_id')
            if aid in ids and (x.get('published_at') or x.get('pub_time')):
                d_hit.add(aid)
    print(f'  D. legacy v1 索引                        : {len(d_hit)}')

    union = a_hit | b_hit | c_hit | d_hit
    print(f'  => 合计可补（去重）: {len(union)} / {len(miss)}   仍缺 {len(miss) - len(union)}')