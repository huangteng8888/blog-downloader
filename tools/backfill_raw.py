# -*- coding: utf-8 -*-
"""为"改造前已抓、无原始字节存档"的产物补抓 raw 快照。

与主抓取脚本的区别：
  - 主脚本按"清单里没有 .md"来决定抓谁；本脚本恰恰相反
    ——只处理**已有 .md 但缺 raw_file 字段**的产物；
  - 只补证据（raw/*.html.gz + raw_sha1_b32 + raw_wrapped），
    绝不覆盖已解析的正文，避免动到既有数据；
  - 同样严格串行 + 同样默认 15 秒间隔，节点轮换逻辑复用主脚本。

用法：
    python tools/backfill_raw.py --dry-run              # 只统计待补数量，不联网
    python tools/backfill_raw.py --gap 15 --seconds 21600
"""
import argparse
import io
import os
import random
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wayback_resume as W  # noqa: E402

POSTS = W.POSTS_DIR
LOG = os.path.join(W.OUT_DIR, "backfill_raw.log")


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line)
    sys.stdout.flush()
    with io.open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def scan_targets():
    out = []
    for fn in sorted(os.listdir(POSTS)):
        if not fn.endswith(".md"):
            continue
        fp = os.path.join(POSTS, fn)
        data = io.open(fp, encoding="utf-8", errors="ignore", newline="").read()
        parts = data.split("---", 2)
        if len(parts) < 3:
            continue
        fm = dict((m.group(1), m.group(2).strip())
                  for m in re.finditer(r"^([a-z0-9_]+):\s*(.*?)\s*$", parts[1], re.M))
        if fm.get("raw_file"):
            continue
        if not (fm.get("snapshot_ts") and fm.get("source_url")):
            continue
        rid = fm["raw_file"] if fm.get("raw_file") else "%s.%s.html.gz" % (fn[:-3], fm["snapshot_ts"])
        if os.path.exists(os.path.join(W.RAW_DIR, rid)):
            continue
        out.append((fn[:-3], fm["snapshot_ts"], fm["source_url"], fp, data, parts))
    return out


def patch_fm(fp, data, parts, raw_rel, raw_hash, wrapped):
    """仅在 front matter 内插入三行证据字段，正文原文不动。"""
    nl = "\r\n" if "\r\n" in data else "\n"
    body = parts[2]
    lines = parts[1].strip().splitlines()
    add = ["raw_file: %s" % raw_rel,
           "raw_sha1_b32: %s" % raw_hash,
           "raw_wrapped: %d" % (1 if wrapped else 0)]
    idx = None
    for i, ln in enumerate(lines):
        if ln.startswith("stage:"):
            idx = i + 1
            break
    if idx is None:
        lines = lines + add
    else:
        lines = lines[:idx] + add + lines[idx:]
    with io.open(fp, "w", encoding="utf-8", newline="") as f:
        f.write("---" + nl + nl.join(lines) + nl + "---" + body)
    # 自检：正文必须逐字未变
    check = io.open(fp, encoding="utf-8", newline="").read().split("---", 2)[2]
    if check != body:
        raise SystemExit("正文被改动，中止: " + fp)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gap", type=float, default=15.0, help="请求间隔秒数（默认 15，勿低于 10）")
    ap.add_argument("--seconds", type=float, default=21600, help="本轮最长运行秒数")
    ap.add_argument("--limit", type=int, default=0, help="本轮最多补几篇（0=不限）")
    ap.add_argument("--max-fail", type=int, default=8, help="连续失败达到该值即提前停止")
    ap.add_argument("--dry-run", action="store_true", help="只统计待补数量，不联网")
    args = ap.parse_args()

    if not os.path.isdir(W.RAW_DIR):
        os.makedirs(W.RAW_DIR)
    targets = scan_targets()
    log("待补原始字节: %d 篇" % len(targets))
    if args.dry_run:
        for t in targets[:5]:
            log("   样例 %s  ts=%s" % (t[0], t[1]))
        log("dry-run 结束（未发起任何请求）")
        return
    if not targets:
        log("无待补项，结束")
        return

    t0 = time.time()
    ok = fail = 0
    consec = 0
    for i, (aid, ts, url, fp, data, parts) in enumerate(targets):
        if args.limit and ok >= args.limit:
            log("达到本轮篇数上限（%d），停止" % args.limit)
            break
        if time.time() - t0 > args.seconds:
            log("达到本轮时限，停止")
            break
        name, tmpl = W.NODES[i % len(W.NODES)]
        if i:
            time.sleep(args.gap + random.uniform(0, args.gap * 0.2))
        try:
            text, delivered, wrapped = W.fetch_one(name, tmpl, ts, url)
        except Exception as e:
            consec += 1
            fail += 1
            log("  失败(%s) %s: %s" % (name, aid, str(e)[:70]))
            if consec >= args.max_fail:
                log("连续失败 %d 次，提前停止（稍后重跑即可续补）" % consec)
                break
            continue
        raw_rel, raw_hash = W.save_raw(aid, ts, delivered)
        if not raw_rel:
            consec += 1
            fail += 1
            log("  空响应或落盘失败: %s" % aid)
            continue
        patch_fm(fp, data, parts, raw_rel, raw_hash, wrapped)
        ok += 1
        consec = 0
        if ok % 10 == 0:
            el = time.time() - t0
            log("  已补 %d 篇（经 %.0fs，%.1f 篇/分钟）" % (ok, el, ok / max(el / 60, 0.01)))

    log("-" * 64)
    log("本轮：补成功 %d | 失败 %d | 耗时 %.0fs" % (ok, fail, time.time() - t0))
    log("剩余待补 %d 篇（重跑本脚本可继续）" % len(scan_targets()))


if __name__ == "__main__":
    main()