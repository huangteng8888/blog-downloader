#!/usr/bin/env python3
"""一次性诊断脚本：检查 post_scorer 产出 top500 的质量（长度偏置 / date 缺失）。"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCORED = ROOT / 'keyword_discovery' / 'scored'

NAMES = {'1215172700': '缠中说禅', '1285707277': '股市风云', '1300871220': '徐小明'}


def summarize(uid):
    f = SCORED / f'{uid}_top500.json'
    if not f.exists():
        print(f'{uid}: 缺文件')
        return
    data = json.loads(f.read_text(encoding='utf-8'))
    n = len(data)
    lens = [d['text_len'] for d in data]
    short = sum(1 for L in lens if L < 150)
    very_short = sum(1 for L in lens if L < 100)
    no_date = sum(1 for d in data if not d.get('date'))
    titles_xu = sum(1 for d in data if d['title'].startswith('徐小明：'))
    print(f'--- {NAMES.get(uid, uid)} ({uid}) : {n} 篇 ---')
    print(f'  长度 <150 字 : {short} ({short/n*100:.1f}%)')
    print(f'  长度 <100 字 : {very_short} ({very_short/n*100:.1f}%)')
    print(f'  长度中位数   : {sorted(lens)[n//2]}')
    print(f'  长度最大     : {max(lens)}')
    print(f'  date 为空的  : {no_date} ({no_date/n*100:.1f}%)')
    print(f'  标题带"徐小明："前缀: {titles_xu}')
    # 短文本是否霸榜
    top50 = data[:50]
    print(f'  前 50 名中长度<150 的: {sum(1 for d in top50 if d["text_len"]<150)}')
    print()


for uid in NAMES:
    summarize(uid)

# 全量 ranked 里，长度与得分的相关性（用前 3000 条估计）
for uid in NAMES:
    f = SCORED / f'{uid}_ranked.json'
    if not f.exists():
        continue
    data = json.loads(f.read_text(encoding='utf-8'))[:3000]
    if not data:
        continue
    import statistics
    sc = [d['raw_score'] for d in data]
    ln = [d['text_len'] for d in data]
    print(f'{NAMES[uid]}: 前3000条 得分中位={statistics.median(sc):.1f}, 长度中位={statistics.median(ln):.0f}')