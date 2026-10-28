# -*- coding: utf-8 -*-
"""离线修复空正文产物（从本地 raw 重解析，不回源站，不动抓取队列）。

背景（2026-10-25）：
  产物目录出现 67 篇正文 <30 字的条目（占 1.9%）。其中 12 篇来自
  "引号被反斜杠转义"的老快照。本工具用修订后的解析器在**本地 raw** 上重解析，
  能把正文找回来的就原子改写产物，并保留原始文件备份。

安全约定：
  - 只读 raw/、skip 清单；只改 posts/ 下确认为空的文件；
  - 先备份到 output/1216826604_wayback/repair_backup/；
  - 原子写入（临时文件 + os.replace），不产生半截文件；
  - 抓取进程与本工具写的是不同分片，且同 id 重写幂等，不冲突。
"""
import glob
import gzip
import io
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "output", "1216826604_wayback")
BK = os.path.join(OUT, "repair_backup")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import wayback_resume as W  # noqa: E402

DRY = "--apply" not in sys.argv


def split_post(t):
    m = re.match(r"(?s)^---\n(.*?)\n---\n?(.*)$", t)
    if not m:
        return None, t
    return m.group(1), m.group(2)


os.makedirs(BK, exist_ok=True)
report = {"scanned": 0, "empty": 0, "repaired": 0, "still_empty": 0, "no_raw": 0, "items": []}

for p in sorted(glob.glob(os.path.join(OUT, "posts", "*.md"))):
    aid = os.path.basename(p)[:-3]
    t = io.open(p, encoding="utf-8", errors="ignore").read()
    fm, body = split_post(t)
    if fm is None or len((body or "").strip()) >= 30:
        continue
    report["scanned"] += 1
    report["empty"] += 1
    raws = sorted(glob.glob(os.path.join(OUT, "raw", "%s.*.html.gz" % aid)))
    if not raws:
        report["no_raw"] += 1
        report["items"].append((aid, "缺 raw", 0))
        continue
    best_st, best_d, best_n = "", None, 0
    for rf in raws:
        try:
            h = gzip.open(rf, "rt", encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        st, d = W.parse_article(h)
        n = len((d or {}).get("content") or "") if isinstance(d, dict) else 0
        if n > best_n:
            best_st, best_d, best_n = st, d, n
    if best_n < 30 or not best_d:
        report["still_empty"] += 1
        report["items"].append((aid, best_st or "无正文", best_n))
        continue
    if not DRY:
        io.open(os.path.join(BK, aid + ".md"), "w", encoding="utf-8").write(t)
        newfm = re.sub(r"\nparse_rev:.*", "", fm)
        newfm += "\nparse_rev: p0-20261025-repair"
        out = "---\n%s\n---\n\n%s\n" % (newfm, best_d["content"])
        tmp = p + ".tmp"
        io.open(tmp, "w", encoding="utf-8").write(out)
        os.replace(tmp, p)
    report["repaired"] += 1
    report["items"].append((aid, best_st, best_n))

print("%s（%s）" % ("试运行，未改动文件" if DRY else "已写入", time.strftime("%H:%M:%S")))
print("扫描空文 %d 篇：修复 %d ，仍无正文 %d ，缺 raw %d"
      % (report["empty"], report["repaired"], report["still_empty"], report["no_raw"]))
print("\n明细（最多 20 条）：")
for aid, st, n in report["items"][:20]:
    print("  %-20s %-12s %d 字" % (aid, st, n))
io.open(os.path.join(ROOT, "temp", "repair_empty_report.json"), "w",
        encoding="utf-8").write(json.dumps(report, ensure_ascii=False, indent=1))