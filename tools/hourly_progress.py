# -*- coding: utf-8 -*-
"""wu2198 补抓进展：按小时统计。

两个数据源交叉验证：
  1) posts/*.md 的 mtime     → 每小时真实落盘篇数（产出）
  2) progress.log 的事件行    → 每小时永久跳过/纯图片/临时失败（过程）
"""
import collections
import glob
import io
import json
import os
import re
import time

ROOT = "D:/cursor_test/blog-downloader"
OUT = os.path.join(ROOT, "output", "1216826604_wayback")
POSTS = os.path.join(OUT, "posts")
PROG = os.path.join(OUT, "progress.log")
LISTF = os.path.join(ROOT, "temp", "wayback_cdx2.json")
SKIPF = os.path.join(OUT, "skip_permanent.json")

# ---- 1. 每小时落盘 ----
md = glob.glob(os.path.join(POSTS, "*.md"))
bymd = collections.Counter()
for f in md:
    bymd[time.strftime("%m-%d %H", time.localtime(os.path.getmtime(f)))] += 1

# ---- 2. 每小时事件 ----
ev = collections.defaultdict(collections.Counter)
last = {}
for ln in io.open(PROG, encoding="utf-8", errors="ignore"):
    m = re.match(r"\[(\d\d):(\d\d):(\d\d)\]", ln)
    if not m:
        continue
    day = time.strftime("%m-%d", time.localtime())
    hh = "%s %s" % (day, m.group(1))
    t = " ".join(ln.split())
    if "永久跳过" in t:
        ev[hh]["skip"] += 1
    elif "纯图片/无正文" in t:
        ev[hh]["image"] += 1
    elif "临时失败" in t:
        ev[hh]["fail"] += 1
    elif "已抓" in t and "篇（经" in t:
        mm = re.search(r"已抓 (\d+) 篇（经 (\d+)s", t)
        if mm:
            ev[hh]["ok_report"] = int(mm.group(1))
            ev[hh]["elapsed"] = int(mm.group(2))
        last["线"] = t
    elif "已抓" in t and "待抓" in t:
        last["启动"] = t

# ---- 3. 汇总 ----
target = len(json.load(io.open(LISTF, encoding="utf-8")).get("items", {}))
try:
    skip_total = len(json.load(io.open(SKIPF, encoding="utf-8")))
except Exception:
    skip_total = 0
done = len(md)

print("=" * 72)
print("wu2198 补抓进展（按小时）  统计时刻 %s" % time.strftime("%Y-%m-%d %H:%M"))
print("=" * 72)
print("快照清单总数 : %d 篇" % target)
print("已落盘       : %d 篇（占清单 %.1f%%）" % (done, 100.0 * done / max(1, target)))
print("永久跳过     : %d 篇" % skip_total)
print("剩余待抓     : %d 篇" % (target - done - skip_total))
print()
print("小时      落盘  永久跳过  纯图片  临时失败   累计落盘  吞吐(篇/分)")
print("-" * 72)
cum = 0
keys = sorted(set(bymd) | set(ev))
rows = []
for k in keys:
    r = bymd.get(k, 0)
    s = ev[k].get("skip", 0)
    i = ev[k].get("image", 0)
    f = ev[k].get("fail", 0)
    cum += r
    rate = r / 60.0
    note = ""
    if k.endswith(" 15") and r:
        note = " ←含12分钟并发(3.6篇/分)"
    print("%s   %4d    %4d    %4d    %4d     %5d     %.2f%s" % (k, r, s, i, f, cum, rate, note))
    rows.append((k, r, s, i, f))

print("-" * 72)
tot_r = sum(x[1] for x in rows)
tot_s = sum(x[2] for x in rows)
tot_i = sum(x[3] for x in rows)
tot_f = sum(x[4] for x in rows)
print("合计       %4d    %4d    %4d    %4d" % (tot_r, tot_s, tot_i, tot_f))
print()
if last.get("线"):
    print("最新吞吐线 : %s" % last["线"])
if last.get("启动"):
    print("最新启动线 : %s" % last["启动"])