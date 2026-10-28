#!/usr/bin/env python3
"""一次性诊断脚本：对比 v1(根目录, hash 命名) 与 v2(output/, article_id 命名) 两代数据。"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def ids_from(dirpath, pattern):
    out = set()
    d = ROOT / dirpath
    if not d.exists():
        return out
    for p in d.glob('*.md'):
        t = p.read_text(encoding='utf-8', errors='ignore')
        m = re.search(pattern, t, re.MULTILINE)
        if m:
            out.add(m.group(1))
    return out


def main():
    pairs = [
        ('1300871220 徐小明', 'legacy/1300871220/posts', 'output/1300871220/posts'),
        ('1215172700 缠中说禅', 'legacy/1215172700/posts_backup', 'output/1215172700/posts'),
        ('1285707277 股市风云', 'legacy/1285707277/posts', 'output/1285707277/posts'),
    ]
    for name, old, new in pairs:
        a = ids_from(old, r'^id:\s*(\S+)')
        b = ids_from(new, r'article_id:\s*(\S+)')
        print(f'--- {name} ---')
        print(f'  v1(根目录) 文章数 : {len(a)}')
        print(f'  v2(output) 文章数 : {len(b)}')
        print(f'  两者都有          : {len(a & b)}')
        print(f'  仅 v1 有          : {len(a - b)}')
        print(f'  仅 v2 有          : {len(b - a)}')
        print()

    # 检查 v1 数据里的 tags 丰富度（这是 v2 丢弃的字段）
    for uid in ('1300871220', '1215172700'):
        d = ROOT / 'legacy' / uid / ('posts' if uid == '1300871220' else 'posts_backup')
        if not d.exists():
            continue
        with_tags = 0
        total = 0
        for p in d.glob('*.md'):
            total += 1
            t = p.read_text(encoding='utf-8', errors='ignore')
            m = re.search(r'^tags:\s*(.+)$', t, re.MULTILINE)
            if m and m.group(1).strip() not in ('[]', ''):
                with_tags += 1
        print(f'v1 {uid}: {with_tags}/{total} 篇带 tags')


if __name__ == '__main__':
    main()