#!/usr/bin/env python3
"""
定向重抓：为缺 pub_time 的文章补回发布时间。

背景：有 232 篇文章是 v1 快照之后抓取的，legacy/ 中任何来源都不含其时间信息，
只能回到新浪详情页重新读取（详情页含 `<span class="time ...">(YYYY-MM-DD HH:MM:SS)</span>`）。

特性：
  - 复用 `src/spider.py` 的 cookie 管理与 curl 调用（含 418 应对）
  - 断点续跑：进度写入 posts/.backfill_sina_progress.json
  - 限速、失败容忍；写入前自动备份 index.json
  - 只填空缺，不覆盖已有 pub_time

用法：
  python tools/backfill_from_sina.py --dry-run --limit 3   # 先小样验证
  python tools/backfill_from_sina.py                       # 全量补齐
  python tools/backfill_from_sina.py --uid 1300871220      # 只处理某博主
"""
import argparse
import importlib.util
import json
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

# 直接加载 src/spider.py，避免与其它模块同名冲突
_spec = importlib.util.spec_from_file_location('src_spider', ROOT / 'src' / 'spider.py')
spider_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(spider_mod)
SinaSpider = spider_mod.SinaSpider

NAMES = {'1300871220': '徐小明', '1215172700': '缠中说禅', '1285707277': '股市风云'}


def extract_pub_time(html):
    m = re.search(r'class="time[^"]*">\(([^<]+)\)</span>', html)
    if not m:
        return ''
    raw = m.group(1).strip()
    # 规范化：2025-06-23 15:04:16
    m2 = re.match(r'(\d{4})-(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2}):(\d{2})', raw)
    if m2:
        y, mo, d, h, mi, s = m2.groups()
        return f'{y}-{int(mo):02d}-{int(d):02d} {int(h):02d}:{mi}:{s}'
    m3 = re.match(r'(\d{4})-(\d{1,2})-(\d{1,2})', raw)
    if m3:
        y, mo, d = m3.groups()
        return f'{y}-{int(mo):02d}-{int(d):02d} 00:00:00'
    return ''


def process(uid, delay, limit, dry_run, retry_failed):
    out_dir = ROOT / 'output' / uid
    posts_dir = out_dir / 'posts'
    index_path = posts_dir / 'index.json'
    if not index_path.exists():
        print(f'  [跳过] 无 {index_path}')
        return

    data = json.loads(index_path.read_text(encoding='utf-8'))
    posts = data.get('posts', [])
    missing = [p for p in posts if not p.get('pub_time')]
    print(f'\n{"="*66}\n{NAMES.get(uid, uid)} ({uid}): {len(missing)} 篇缺 pub_time\n{"="*66}')

    if not missing:
        print('  无缺失，跳过')
        return

    prog_path = posts_dir / '.backfill_sina_progress.json'
    prog = json.loads(prog_path.read_text(encoding='utf-8')) if prog_path.exists() else {}
    if retry_failed:
        # 丢掉此前失败（空值）的记录，使其重新进入待抓队列
        prog = {k: v for k, v in prog.items() if v}

    todo = [p for p in missing if p['article_id'] not in prog]
    if limit:
        todo = todo[:limit]
    print(f'  进度文件已有 {len(prog)} 条；本次待抓 {len(todo)} 篇')

    if dry_run:
        for p in todo[:5]:
            url = f'https://blog.sina.com.cn/s/blog_{p["article_id"]}.html'
            print(f'    [dry-run] 将抓取 {url}')

    spider = SinaSpider(uid, output_dir=str(posts_dir))

    ok = fail = 0
    fetched = {}
    for i, p in enumerate(todo, 1):
        aid = p['article_id']
        url = f'https://blog.sina.com.cn/s/blog_{aid}.html'
        try:
            html = spider.fetch_article(url)
            pub = extract_pub_time(html)
            if pub:
                prog[aid] = pub
                fetched[aid] = pub
                ok += 1
            else:
                prog[aid] = ''
                fail += 1
        except Exception as e:
            prog[aid] = ''
            fail += 1
            print(f'    [{i}] {aid} 失败: {type(e).__name__}')

        if i % 20 == 0 or i == len(todo):
            print(f'    {i}/{len(todo)}  成功 {ok}  失败 {fail}')
            if not dry_run:
                prog_path.write_text(json.dumps(prog, ensure_ascii=False, indent=2),
                                     encoding='utf-8')

        if not dry_run and i < len(todo):
            time.sleep(delay)

    if dry_run:
        print('  [dry-run] 未写入')
        return

    # 应用回填
    if fetched:
        bak = posts_dir / 'index.json.bak'
        if not bak.exists():
            shutil.copy2(index_path, bak)
        for p in posts:
            pub = fetched.get(p['article_id'])
            if pub and not p.get('pub_time'):
                p['pub_time'] = pub
                p['pub_date'] = pub[:10]
                p['timestamp'] = int(spider_mod.datetime.strptime(
                    pub, '%Y-%m-%d %H:%M:%S').timestamp())
        data['updated_at'] = spider_mod.datetime.now().isoformat()
        index_path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                              encoding='utf-8')
        print(f'  已回填 {len(fetched)} 篇，index.json 已更新（旧文件备份为 index.json.bak）')

    left = sum(1 for p in posts if not p.get('pub_time'))
    print(f'  剩余缺失: {left}/{len(posts)}  ({left/len(posts)*100:.2f}%)')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--uid', default=None)
    ap.add_argument('--delay', type=float, default=1.0)
    ap.add_argument('--limit', type=int, default=None)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--retry-failed', action='store_true', help='重试此前失败的条目')
    args = ap.parse_args()

    uids = [args.uid] if args.uid else list(NAMES)
    for uid in uids:
        process(uid, args.delay, args.limit, args.dry_run, args.retry_failed)


if __name__ == '__main__':
    main()