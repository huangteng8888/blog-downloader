#!/usr/bin/env python3
"""
残余元数据回填：处理 backfill_from_legacy / recover_from_v1_graph 之后的剩余缺口。

可用来源：
  1. legacy/{uid}/posts/index.json —— v1 旧索引（字段名：id / published_at / tags）
  2. legacy/{uid}/blog-graph/analysis_results.json —— AI 分析产物，
     含 _source_file（= md5(文章URL)）与 _published_at。
     v1 的文件名规律经实测验证为 md5(f'https://blog.sina.com.cn/s/blog_{{aid}}.html')，
     因此可由 article_id 反算 hash 后关联。

用法：
  python tools/backfill_residual.py --dry-run
  python tools/backfill_residual.py
"""
import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

from metadata import BlogMetadata  # noqa: E402


def url_hash(aid: str) -> str:
    return hashlib.md5(f'https://blog.sina.com.cn/s/blog_{aid}.html'.encode()).hexdigest()


def load_v1_index(rel):
    """legacy v1 索引 -> {article_id: {published_at, tags}}"""
    p = ROOT / rel
    out = {}
    if not p.exists():
        return out
    data = json.loads(p.read_text(encoding='utf-8', errors='ignore'))
    posts = data.get('posts', data) if isinstance(data, dict) else data
    for x in posts:
        aid = x.get('id') or x.get('article_id')
        if not aid:
            continue
        out[aid] = {
            'published_at': x.get('published_at') or x.get('pub_time') or '',
            'tags': x.get('tags') or [],
        }
    return out


def load_analysis(rel):
    """analysis_results.json -> {hash_filename: {published_at, author}}"""
    p = ROOT / rel
    out = {}
    if not p.exists():
        return out
    data = json.loads(p.read_text(encoding='utf-8', errors='ignore'))
    for x in data if isinstance(data, list) else data.get('results', []):
        sf = (x.get('_source_file') or '').replace('.md', '')
        if sf:
            out[sf] = {
                'published_at': x.get('_published_at') or '',
                'author': x.get('_author') or '',
            }
    return out


# uid -> (v1 索引相对路径, analysis_results 相对路径)
# 注：只使用权威来源（v1 frontmatter 的 published_at 派生数据）。
# 已验证不可用/不可信的来源：
#   - legacy md 正文正则取日期 -> 实际匹配到 fetched_at（抓取时间），会注入错误日期
#   - 旧打分产物的 date         -> 来自正文首个 ISO 日期，可能是链接文章的日期
SOURCES = {
    '1300871220': ('legacy/1300871220/posts/index.json',
                   'legacy/1300871220/blog-graph/analysis_results.json'),
    '1215172700': (None,
                   'legacy/1215172700/blog-graph/analysis_results.json'),
    '1285707277': (None,
                   'legacy/1285707277/blog-graph/analysis_results.json'),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    for uid, (v1_rel, an_rel) in SOURCES.items():
        print(f'\n{"="*66}\n{uid}\n{"="*66}')
        out_dir = ROOT / 'output' / uid
        posts_dir = out_dir / 'posts'
        index_path = posts_dir / 'index.json'
        if not index_path.exists():
            print('  [跳过] 无 index.json')
            continue

        meta = BlogMetadata(out_dir)
        meta.init_posts_index()
        total = len(meta.posts)
        missing = [p for p in meta.posts if not p.get('pub_time')]
        print(f'  当前 {total} 篇，缺 pub_time 的 {len(missing)} 篇 ({len(missing)/total*100:.2f}%)')
        if not missing:
            continue

        v1 = load_v1_index(v1_rel) if v1_rel else {}
        an = load_analysis(an_rel) if an_rel else {}
        print(f'  v1 索引可用记录: {len(v1)}   analysis 可用记录: {len(an)}')

        filled_time = filled_tags = 0
        for p in missing:
            aid = p['article_id']
            src = v1.get(aid)
            if src and src['published_at']:
                p['pub_time'] = src['published_at']
                p['pub_date'] = src['published_at'][:10]
                p['timestamp'] = BlogMetadata._parse_timestamp(src['published_at'])
                filled_time += 1
                if not p.get('tags') and src['tags']:
                    p['tags'] = src['tags']
                    filled_tags += 1
                continue
            if an:
                rec = an.get(url_hash(aid))
                if rec and rec['published_at']:
                    p['pub_time'] = rec['published_at']
                    p['pub_date'] = rec['published_at'][:10]
                    p['timestamp'] = BlogMetadata._parse_timestamp(rec['published_at'])
                    filled_time += 1
                    if not p.get('author_name') and rec['author']:
                        p['author_name'] = rec['author']

        print(f'  本次回填 pub_time: {filled_time}   tags: {filled_tags}')

        if args.dry_run:
            print('  [dry-run] 未写入')
            continue

        if filled_time or filled_tags:
            bak = posts_dir / 'index.json.bak'
            if not bak.exists():
                shutil.copy2(index_path, bak)
            meta._dirty = True
            meta.save()

        after_missing = sum(1 for p in meta.posts if not p.get('pub_time'))
        n = len(meta.posts)
        print(f'  写入完成: 缺 pub_time 降至 {after_missing}/{n} '
              f'({after_missing/n*100:.2f}%)，覆盖率 {(n-after_missing)/n*100:.2f}%')


if __name__ == '__main__':
    main()