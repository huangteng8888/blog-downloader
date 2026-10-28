# -*- coding: utf-8 -*-
"""把复验目录的结果合并回 staging（人工核对后再执行）。

定位（2026-10-25）：
  复验通道 tools/recheck_landed_empty.py 把 552 条重取结果写到
  output/1216826604_wayback_recheck/posts/，**刻意不碰主库**（用户决定：
  先落新文件、人工核对后再合并）。本工具就是那个"合并"动作，且默认 dry-run。

合并规则（按 recheck_kind 分流）：
  - skipped（485 条，此前未落盘）→ 直接复制进 staging `1216826604_wayback/posts/`；
  - landed_empty（67 条，此前落盘但空）→ **默认只报告、不改动**；
    加 --replace-empty 才用复验结果替换，且替换前把原文件备份进 merge_backup/。
  - staging 中已有**非空**同名产物 → 一律跳过并记冲突（不覆盖任何既有正文）。

安全约定：
  - 默认 dry-run（--apply 才真正写）；
  - 主线抓取活跃时拒跑（复用复验工具的双证据判据），避免与主线写同一目录；
  - 输出 merge_recheck_manifest.csv 记录每一条的处置，可回溯。

合并完成后需重跑 `tools/merge_wayback.py` 刷新 output/1216826604_merged。
"""
import argparse
import csv
import io
import os
import re
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from recheck_landed_empty import main_crawler_active  # noqa: E402

RECHECK_POSTS = os.path.join(ROOT, "output", "1216826604_wayback_recheck", "posts")
STAGE = os.path.join(ROOT, "output", "1216826604_wayback", "posts")
BACKUP = os.path.join(ROOT, "output", "1216826604_wayback", "merge_backup")
MANIFEST = os.path.join(ROOT, "output", "1216826604_wayback", "merge_recheck_manifest.csv")


def split_fm(text):
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not m:
        return {}, text
    return dict(re.findall(r"^([\w_]+):\s*(.*)$", m.group(1), re.M)), m.group(2)


def body_len(t):
    fm, b = split_fm(t)
    return len((b or "").strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正执行合并（默认 dry-run）")
    ap.add_argument("--replace-empty", action="store_true",
                    help="允许用复验结果替换 staging 中的空正文产物（先备份）")
    ap.add_argument("--force", action="store_true", help="主线活跃时也强制执行（危险）")
    args = ap.parse_args()

    if not os.path.isdir(RECHECK_POSTS):
        print("复验目录不存在或还没有产物：%s" % RECHECK_POSTS)
        return
    files = sorted(f for f in os.listdir(RECHECK_POSTS) if f.endswith(".md"))
    print("复验产物：%d 篇" % len(files))

    active = main_crawler_active()
    print("主线活跃判据：%s" % ("；".join(active) if active else "两条证据均未命中"))
    if args.apply and active and not args.force:
        print("主线正在抓取，拒绝合并（会与主线同时写 staging 目录）。等主线停下或加 --force。")
        return

    rows = []
    n_new = n_repl = n_conflict = n_skip_empty = n_bad = 0
    for f in files:
        src = os.path.join(RECHECK_POSTS, f)
        t = io.open(src, encoding="utf-8", errors="ignore").read()
        fm, _ = split_fm(t)
        aid = fm.get("article_id", f[:-3])
        kind = fm.get("recheck_kind", "")
        blen = body_len(t)
        dst = os.path.join(STAGE, f)
        if blen < 30:
            n_bad += 1
            rows.append((aid, kind, "坏产物(正文<30字)", blen, dst if os.path.exists(dst) else ""))
            continue
        if not os.path.exists(dst):
            n_new += 1
            act = "新增入 staging"
        else:
            dlen = body_len(io.open(dst, encoding="utf-8", errors="ignore").read())
            if dlen >= 30:
                n_conflict += 1
                rows.append((aid, kind, "冲突：staging 已有非空产物，跳过", blen, dst))
                continue
            if not args.replace_empty:
                n_skip_empty += 1
                rows.append((aid, kind, "待定：staging 为空白，未加 --replace-empty 故不动", blen, dst))
                continue
            n_repl += 1
            act = "替换空白产物（先备份）"
        rows.append((aid, kind, act, blen, dst))
        if args.apply:
            if os.path.exists(dst):
                if not os.path.isdir(BACKUP):
                    os.makedirs(BACKUP)
                shutil.copy2(dst, os.path.join(BACKUP, f))
            tmp = dst + ".tmp"
            io.open(tmp, "w", encoding="utf-8").write(t)
            os.replace(tmp, dst)

    print("\n%s" % ("已执行" if args.apply else "试运行（未改动任何文件）"))
    print("  新增入 staging：%d" % n_new)
    print("  替换空白产物  ：%d" % n_repl)
    print("  冲突跳过      ：%d" % n_conflict)
    print("  空白待定      ：%d" % n_skip_empty)
    print("  坏产物        ：%d" % n_bad)
    if args.apply:
        with io.open(MANIFEST, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["article_id", "recheck_kind", "action", "recheck_chars", "target"])
            w.writerows(rows)
        print("  处置清单已写：%s" % MANIFEST)
        print("\n提示：合并后需重跑 tools/merge_wayback.py 刷新 output/1216826604_merged。")


if __name__ == "__main__":
    main()