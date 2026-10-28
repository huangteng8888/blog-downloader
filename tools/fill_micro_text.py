# -*- coding: utf-8 -*-
"""把"微文/投票贴"的唯一一句话补回产物正文（离线工具，默认 dry-run）。

背景（2026-10-25 22:0x 定性）：staging 里有 154 篇空正文产物，逐篇核对 raw 后确认
**没有成段正文被丢弃**，它们是：
  - 投票贴 80 篇（"下午会拉大阳吗？你看呢？" + 投票控件）
  - 微文贴 54 篇（正文容器内 20~38 字的一句话 + "文章引用自："）
  - 图片贴 20 篇（容器不存在或无 raw，正文确实为空）
但解析器把 <30 字的正文整段丢成 0 字，导致这些帖子**唯一的内容也丢了**。
本工具把这段短文本补回正文，并打标 `body_source: micro`，不动解析器、不影响在跑的抓取。

用法：
    python tools/fill_micro_text.py                  # 试运行：只统计与举例
    python tools/fill_micro_text.py --limit 5        # 只看 5 篇
    python tools/fill_micro_text.py --apply           # 真正写回（原文件备份）
安全：只处理正文 <30 字的产物；覆盖前备份到 micro_backup/；原子写入；
      文本 >200 字的异常情形拒绝写入并列入待人工清单。
"""
import argparse
import glob
import gzip
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "output", "1216826604_wayback")
POSTS = os.path.join(OUT, "posts")
RAW = os.path.join(OUT, "raw")
BAK = os.path.join(OUT, "micro_backup")
REPORT = os.path.join(OUT, "micro_fill_report.json")
sys.path.insert(0, os.path.join(ROOT, "tools"))
import wayback_resume as W  # noqa: E402

MIN_KEEP = 8          # 少于 8 字视为噪声，不补
MAX_KEEP = 200        # 超过 200 字说明不是微文，交人工


def container_text(h):
    """取正文容器内的纯文本；容器未命中返回 None。"""
    m = None
    for pat in W.OPEN_PATS:
        m = re.search(pat, h, re.I)
        if m:
            break
    if not m:
        return None
    seg = h[m.end():m.end() + 20000]
    depth, cut = 1, 0
    for t in re.finditer(r"(?i)<(/?)div\b[^>]*>", seg):
        depth += -1 if t.group(1) else 1
        if depth == 0:
            cut = t.start()
            break
    seg = seg[:cut] if cut else seg
    txt = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", seg)
    txt = re.sub(r"(?s)<[^>]+>", " ", txt).replace("&nbsp;", " ")
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt


def split_fm(text):
    m = re.match(r"(?s)^---\n(.*?)\n---\n?(.*)$", text)
    if not m:
        return None, None, text
    return m.group(1), m.group(2), text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写回（默认 dry-run）")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 篇（用于抽查）")
    args = ap.parse_args()

    rows, done, skipped = [], 0, []
    for p in sorted(glob.glob(os.path.join(POSTS, "*.md"))):
        aid = os.path.basename(p)[:-3]
        t = io.open(p, encoding="utf-8", errors="ignore").read()
        fm, body, _ = split_fm(t)
        if fm is None or len((body or "").strip()) >= 30:
            continue
        raws = glob.glob(os.path.join(RAW, "%s.*.html.gz" % aid))
        if not raws:
            skipped.append((aid, "无 raw"))
            continue
        h = gzip.open(raws[0], "rt", encoding="utf-8", errors="ignore").read()
        txt = container_text(h) or ""
        txt = re.sub(r"\s*文章引用自：\s*$", "", txt).strip()
        if not txt:
            skipped.append((aid, "容器内无文本"))
            continue
        if len(txt) > MAX_KEEP:
            skipped.append((aid, "文本 %d 字，超限交人工" % len(txt)))
            continue
        if len(txt) < MIN_KEEP:
            skipped.append((aid, "仅 %d 字，视为噪声" % len(txt)))
            continue
        rows.append((aid, p, fm, txt, h))
        if args.limit and len(rows) >= args.limit:
            break

    print("待补微文：%d 篇（备份目录 %s）" % (len(rows), os.path.relpath(BAK, ROOT)))
    by_kind = {}
    for aid, p, fm, txt, h in rows:
        kind = "投票贴" if re.search(r"投票|发起时间", h) else "微文"
        by_kind[kind] = by_kind.get(kind, 0) + 1
    for k, v in sorted(by_kind.items()):
        print("   %s：%d 篇" % (k, v))
    print("   跳过：%d 篇（多为图片贴，正文确实为空）" % len(skipped))

    for aid, p, fm, txt, h in rows[:6]:
        print("   %s  %d 字  %s" % (aid, len(txt), txt[:60]))

    if not args.apply:
        print("\n试运行结束（未改动任何文件）。加 --apply 才会写回。")
        return

    os.makedirs(BAK, exist_ok=True)
    for aid, p, fm, txt, h in rows:
        orig = io.open(p, encoding="utf-8", errors="ignore").read()
        io.open(os.path.join(BAK, aid + ".md"), "w", encoding="utf-8").write(orig)
        new_fm = fm.rstrip("\n") + "\nbody_source: micro\nmicro_chars: %d" % len(txt)
        out = "---\n%s\n---\n\n%s\n" % (new_fm, txt)
        tmp = p + ".tmp"
        io.open(tmp, "w", encoding="utf-8").write(out)
        os.replace(tmp, p)
        done += 1
    with io.open(REPORT, "w", encoding="utf-8") as f:
        json.dump({"filled": done, "skipped": len(skipped),
                   "ids": [r[0] for r in rows],
                   "skip_detail": skipped[:200]}, f, ensure_ascii=False, indent=1)
    print("\n已补写 %d 篇；备份 %d 份；报告 %s" % (done, done, os.path.relpath(REPORT, ROOT)))


if __name__ == "__main__":
    main()