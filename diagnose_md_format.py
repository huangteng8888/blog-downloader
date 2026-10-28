#!/usr/bin/env python3
"""一次性诊断脚本：确认 output 目录里 md 文件的格式分布，
验证「有 pub_time 的那 86 篇来自带元数据的旧格式文件」这一假设。"""
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
UID = '1300871220'
posts_dir = ROOT / 'output' / UID / 'posts'

idx = json.loads((posts_dir / 'index.json').read_text(encoding='utf-8'))
with_time = {p['article_id'] for p in idx['posts'] if p.get('pub_time')}

fm_keys = Counter()
has_time_fm = 0
no_fm = 0
total = 0
rich_examples = []

for md in posts_dir.glob('*.md'):
    total += 1
    text = md.read_text(encoding='utf-8', errors='ignore')
    m = re.match(r'^---\n(.*?)\n---', text, re.DOTALL)
    if not m:
        no_fm += 1
        continue
    keys = tuple(sorted(
        line.split(':', 1)[0].strip()
        for line in m.group(1).split('\n') if ':' in line
    ))
    fm_keys[keys] += 1
    if 'published_at' in m.group(1) or 'pub_time' in m.group(1):
        has_time_fm += 1
        if len(rich_examples) < 3:
            rich_examples.append(md.name)

print(f'output/{UID}/posts 共 {total} 个 md')
print(f'  无 frontmatter      : {no_fm}')
print(f'  frontmatter 含日期字段: {has_time_fm}')
print(f'  index.json 中有 pub_time 的文章数: {len(with_time)}')
print()
print('frontmatter 字段组合 Top5:')
for keys, cnt in fm_keys.most_common(5):
    print(f'  {cnt:6d} × {list(keys)}')
print()
print('含日期字段的样例文件:', rich_examples)
print('这些样例是否在 index 的 pub_time 集合内:',
      [Path(x).stem in with_time for x in rich_examples])

# 检查盖率交集
rich_stems = {
    md.stem for md in posts_dir.glob('*.md')
    if re.search(r'^(published_at|pub_time):', md.read_text(encoding='utf-8', errors='ignore'), re.MULTILINE)
}
print(f'含日期字段的文件(stem) 数: {len(rich_stems)}')
print(f'与 index 有 pub_time 的交集: {len(rich_stems & with_time)}')