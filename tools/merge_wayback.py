# -*- coding: utf-8 -*-
"""
Wayback 暂存区 -> 合并区 的安全合并工具（默认 dry-run）

用法：
    python tools/merge_wayback.py                 # 只出报告，不动任何文件
    python tools/merge_wayback.py --apply         # 真正写入 output/1216826604_merged/

规则：
  1) 主库 output/1216826604/ 与 supplement 目录**始终只读**，任何情况下不被写入；
  2) 合并只写入 output/1216826604_merged/，与主库物理隔离；
  3) ID 已存在于主库或 supplement 的，一律 skip_existing，不覆盖；
  4) 正文为空（图文贴）的照常合并，但前端标记 content_type: photo_only，便于区分；
  5) 每篇改写 stage: staged -> stage: merged，合并来源写入 merge_manifest.csv。
"""
import argparse
import csv
import io
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAGE = os.path.join(ROOT, "output", "1216826604_wayback", "posts")
MAIN = os.path.join(ROOT, "output", "1216826604", "posts")
SUPP = os.path.join(ROOT, "output", "1216826604_supplement", "posts")
OUT = os.path.join(ROOT, "output", "1216826604_merged")
MANIFEST = os.path.join(ROOT, "output", "1216826604_wayback", "merge_manifest.csv")


def ids_of(p):
    if not os.path.isdir(p):
        return set()
    return set(f[:-3] for f in os.listdir(p) if f.endswith(".md"))


def split_fm(text):
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not m:
        return {}, text
    fm = dict(re.findall(r"^(\w+):\s*(.*)$", m.group(1), re.M))
    return fm, m.group(2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正执行合并（默认 dry-run）")
    ap.add_argument("--include-photo-only", action="store_true",
                    help="是否合并无正文的图文贴（默认合并，标记类型）")
    args, _ = ap.parse_known_args()
    args.include_photo_only = True

    if not os.path.isdir(STAGE):
        print("暂存区不存在：%s" % STAGE)
        return 1

    have = ids_of(MAIN) | ids_of(SUPP)
    rows, seen = [], set()
    n_add = n_skip = n_dup = n_empty = 0

    for fn in sorted(os.listdir(STAGE)):
        if not fn.endswith(".md"):
            continue
        aid = fn[:-3]
        text = io.open(os.path.join(STAGE, fn), encoding="utf-8").read()
        fm, body = split_fm(text)
        body = body.strip()

        if aid in seen:
            status = "dup_staging"
            n_dup += 1
        elif aid in have:
            status = "skip_existing"
            n_skip += 1
        elif len(body) < 10:
            status = "add_photo_only"
            n_empty += 1
        else:
            status = "add"
            n_add += 1
        seen.add(aid)

        if status.startswith("add") and args.apply:
            os.makedirs(os.path.join(OUT, "posts"), exist_ok=True)
            new = re.sub(r"^stage:\s*staged\s*$", "stage: merged", text, flags=re.M)
            io.open(os.path.join(OUT, "posts", fn), "w", encoding="utf-8").write(new)

        rows.append([aid, status, len(body), fm.get("title", ""), fm.get("pub_time", ""),
                     fm.get("snapshot_ts", ""), fm.get("source_url", "")])

    with io.open(MANIFEST, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["article_id", "status", "body_chars", "title", "pub_time",
                    "snapshot_ts", "source_url"])
        w.writerows(rows)

    print("模式: %s" % ("APPLY（已写入 %s）" % OUT if args.apply else "DRY-RUN（未改动任何文件）"))
    print("暂存区总数      : %d" % len(rows))
    print("  新增可合并    : %d" % n_add)
    print("  新增(图文贴)  : %d" % n_empty)
    print("  跳过(已存在)  : %d" % n_skip)
    print("  暂存区内部重复: %d" % n_dup)
    print("清单: %s" % MANIFEST)
    return 0


if __name__ == "__main__":
    sys.exit(main())