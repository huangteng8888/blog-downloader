# -*- coding: utf-8 -*-
"""
互联网档案馆一键补抓工具（换到可访问 archive.org 的网络后使用）

功能：
    1. 用 CDX API 列出 wu2198 原博客所有被存档过的文章页
    2. 与本地已采集的 2404 篇对比，算出"可补回多少篇"
    3. 可选：按快照批量下载正文（限速、断点续跑、自动解析）

用法：
    python tools/wayback_fetch.py --scan                 # 只扫描，看有多少存档
    python tools/wayback_fetch.py --scan --match         # 扫描 + 与本地清单比对
    python tools/wayback_fetch.py --fetch --limit 50     # 下载前 50 篇
    python tools/wayback_fetch.py --fetch                # 下载全部（慢，建议分批）

依赖：仅标准库（urllib）。需要能访问 web.archive.org 的网络。
"""
import argparse
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36")

BLOG_ID_PREFIX = "blog_48874cec"          # wu2198 的文章 ID 前缀
CDX = ("https://web.archive.org/cdx/search/cdx"
       "?url=blog.sina.com.cn/s/{prefix}*&output=text&filter=statuscode:200"
       "&collapse=urlkey&limit=100000")
CDX_ALT = ("https://web.archive.org/cdx/search/cdx"
           "?url=blog.sina.com.cn/s/{prefix}*&output=text&limit=100000")

OUT_DIR = os.path.join(ROOT, "output", "1216826604_wayback")
POSTS_DIR = os.path.join(OUT_DIR, "posts")
RESULT_FILE = os.path.join(ROOT, "temp", "wayback_scan.json")
LOCAL_POSTS = os.path.join(ROOT, "output", "1216826604", "posts")


def get(url, timeout=60, retries=3):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")
        except Exception as e:
            print("    ! 请求失败(%d/%d): %s" % (i + 1, retries, str(e)[:60]))
            time.sleep(3 * (i + 1))
    return ""


def scan(alt=False):
    """列出所有被存档的文章页"""
    url = (CDX_ALT if alt else CDX).format(prefix=BLOG_ID_PREFIX)
    print("查询 CDX API ...")
    print("  %s" % url[:120])
    txt = get(url)
    if not txt:
        print("  ✗ 无响应。请确认当前网络能访问 web.archive.org")
        return []

    rows = []
    for line in txt.strip().split("\n"):
        parts = line.split()
        if len(parts) < 3:
            continue
        ts, orig = parts[1], parts[2]
        m = re.search(r"(blog_\w+)\.html", orig)
        if m:
            rows.append({"timestamp": ts, "url": orig, "article_id": m.group(1)})
    print("  ✓ 获得存档记录 %d 条（去重后 %d 篇）"
          % (len(rows), len(set(r["article_id"] for r in rows))))

    with io.open(RESULT_FILE, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print("  已保存: %s" % RESULT_FILE)
    return rows


def match_local(rows):
    """与本地已采集对比"""
    local = set()
    if os.path.isdir(LOCAL_POSTS):
        local = set(f[:-3] for f in os.listdir(LOCAL_POSTS) if f.endswith(".md"))
    ids = set(r["article_id"] for r in rows)
    missing = ids - local
    print("-" * 70)
    print("本地已采集      : %d 篇" % len(local))
    print("档案馆有存档    : %d 篇" % len(ids))
    print("★ 可补回（存档有、本地没有）: %d 篇" % len(missing))
    print("  其中本地已有的                : %d 篇" % len(ids & local))
    if missing:
        with io.open(os.path.join(ROOT, "temp", "wayback_missing.txt"), "w",
                     encoding="utf-8") as f:
            f.write("\n".join(sorted(missing)))
        print("  待补清单已保存: temp/wayback_missing.txt")
    return missing


def body_of(html):
    """复用项目解析逻辑：h2.titName / span.time / div.articalContent"""
    t = re.search(r'<h2[^>]+class="titName[^"]*"[^>]*>([^<]+)</h2>', html)
    d = re.search(r'class="time[^"]*"[^>]*>\(([^<]+)\)', html)
    c = re.search(r'<div[^>]+class="articalContent[^"]*"[^>]*>(.*?)<!-- 正文结束 -->',
                  html, re.DOTALL)
    content = ""
    if c:
        content = re.sub(r"<[^>]+>", "", c.group(1))
        content = content.replace("&nbsp;", " ")
        content = re.sub(r"\s+", " ", content).strip()
    return {
        "title": t.group(1).strip() if t else "",
        "pub_time": d.group(1).strip() if d else "",
        "content": content,
    }


def fetch(rows, limit, delay):
    if not os.path.isdir(POSTS_DIR):
        os.makedirs(POSTS_DIR)
    local = set()
    if os.path.isdir(LOCAL_POSTS):
        local = set(f[:-3] for f in os.listdir(LOCAL_POSTS) if f.endswith(".md"))

    todo = [r for r in rows if r["article_id"] not in local]
    if limit:
        todo = todo[:limit]
    print("待抓取 %d 篇（限速 %.1fs/篇，预计 %.0f 分钟）"
          % (len(todo), delay, len(todo) * (delay + 3) / 60.0))

    index, ok, fail = [], 0, 0
    for i, r in enumerate(todo, 1):
        fp = os.path.join(POSTS_DIR, r["article_id"] + ".md")
        if os.path.exists(fp) and os.path.getsize(fp) > 0:
            continue
        snap = "https://web.archive.org/web/%s/%s" % (r["timestamp"], r["url"])
        html = get(snap, timeout=90, retries=2)
        if not html:
            fail += 1
            print("  [%d/%d] ✗ %s" % (i, len(todo), r["article_id"]))
            time.sleep(delay)
            continue
        p = body_of(html)
        if not p["content"]:
            fail += 1
            print("  [%d/%d] ✗ 解析为空 %s" % (i, len(todo), r["article_id"]))
            time.sleep(delay)
            continue
        with io.open(fp, "w", encoding="utf-8") as f:
            f.write("---\narticle_id: %s\n---\n\n%s\n" % (r["article_id"], p["content"]))
        index.append({"article_id": r["article_id"], "title": p["title"],
                      "pub_time": p["pub_time"], "source_url": r["url"],
                      "snapshot": snap, "chars": len(p["content"])})
        ok += 1
        if ok % 10 == 0:
            print("  [%d/%d] ✓ 已抓 %d 篇" % (i, len(todo), ok))
        time.sleep(delay)

    with io.open(os.path.join(OUT_DIR, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"source": "web.archive.org", "total": len(index), "posts": index},
                  f, ensure_ascii=False, indent=1)
    print("-" * 70)
    print("完成：成功 %d 篇，失败 %d 篇" % (ok, fail))
    print("产物目录: %s" % OUT_DIR)


def main():
    ap = argparse.ArgumentParser(description="互联网档案馆一键补抓（wu2198）")
    ap.add_argument("--scan", action="store_true", help="扫描档案馆存档情况")
    ap.add_argument("--alt", action="store_true", help="使用不带 statuscode 筛选的 CDX 查询")
    ap.add_argument("--match", action="store_true", help="与本地已采集清单比对")
    ap.add_argument("--fetch", action="store_true", help="下载快照正文")
    ap.add_argument("--limit", type=int, default=0, help="最多下载多少篇")
    ap.add_argument("--delay", type=float, default=1.5, help="每篇间隔秒数（请勿过小）")
    args = ap.parse_args()

    if not os.path.isdir(OUT_DIR):
        os.makedirs(OUT_DIR)

    rows = []
    if os.path.exists(RESULT_FILE) and not args.scan:
        rows = json.load(io.open(RESULT_FILE, encoding="utf-8"))
        print("复用已有扫描结果: %d 条" % len(rows))
    if args.scan or not rows:
        rows = scan(args.alt)
    if args.match:
        match_local(rows)
    if args.fetch:
        fetch(rows, args.limit, args.delay)
    if not (args.scan or args.match or args.fetch):
        print("请指定 --scan / --match / --fetch")


if __name__ == "__main__":
    main()