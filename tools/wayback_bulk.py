# -*- coding: utf-8 -*-
"""
通过公开反向代理从 archive.org 批量补抓 wu2198 原文（全量模式）

优化点（相比上一版）：
    - 超时降到 20s、重试降到 2 次，失败快速跳过（避免长时间卡在坏链上）
    - 4 个公开反代轮换，分散单点限流
    - 已失败的不再重复尝试（记入 failed.json）
    - 断点续跑：已存在的 .md 直接跳过

用法：
    python tools/wayback_bulk.py --seconds 780 --workers 8
"""
import argparse
import io
import json
import os
import random
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIST_FILE = os.path.join(ROOT, "temp", "wayback_cdx2.json")
OUT_DIR = os.path.join(ROOT, "output", "1216826604_wayback")
POSTS_DIR = os.path.join(OUT_DIR, "posts")
META_FILE = os.path.join(OUT_DIR, "index.json")
FAIL_FILE = os.path.join(OUT_DIR, "failed.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36")

PROXY_TMPLS = [
    "https://fd-gally.netlify.app/proxy/{raw}",
    "https://cors-http.netlify.app/.netlify/functions/api?url={enc}",
    "https://nocors.pages.dev/proxy?url={enc}",
    "https://allallow.pages.dev/?url={enc}&mode=full",
]

_lock = threading.Lock()
_stats = {"ok": 0, "fail": 0, "skip": 0}
_failed_ids = set()
_rr = {"i": 0}


def log(m):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), m))
    sys.stdout.flush()


def fetch_snapshot(timestamp, url, retries=4, timeout=20):
    """多代理轮询下载；遇到 SSL/超时等通道错误立即换下一个代理"""
    target = "https://web.archive.org/web/%s/%s" % (timestamp, url)
    enc = urllib.parse.quote(target, safe="")
    with _lock:
        start = _rr["i"]
        _rr["i"] = (_rr["i"] + 1) % len(PROXY_TMPLS)
    order = [PROXY_TMPLS[(start + k) % len(PROXY_TMPLS)] for k in range(len(PROXY_TMPLS))]
    for i in range(min(retries, len(order))):
        pu = order[i].format(raw=target, enc=enc)
        try:
            req = urllib.request.Request(pu, headers={"User-Agent": UA,
                                                      "Accept-Language": "zh-CN,zh;q=0.9"})
            raw = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")
            if not raw or "Temporarily Offline" in raw:
                continue
            try:
                d = json.loads(raw)
                c = d.get("content", "")
                if c and len(c) > 2000:
                    return c
            except Exception:
                if len(raw) > 2000:
                    return raw
        except Exception as e:
            # SSL 错误 / 连接问题 → 立即换下一个代理
            msg = str(e)
            if "WRONG_VERSION_NUMBER" in msg or "handshake" in msg or "timed out" in msg:
                continue
            continue
    return ""


def parse_article(html):
    """
    兼容两版模板：
      新版：h2.titName + div.articalContent ... <!-- 正文结束 -->
      旧版(2008前后)：<title>标题_博主_新浪博客 + div.articleContent / #articleBody / .postcontent
    """
    if "设置了访问权限" in html or "该文章已经被加密" in html:
        return None

    # ---- 标题 ----
    title = ""
    m = re.search(r'<h2[^>]+class="titName[^"]*"[^>]*>([^<]+)</h2>', html)
    if m and m.group(1).strip():
        title = m.group(1).strip()
    if not title:
        m = re.search(r'class="[^"]*articleTitle[^"]*"[^>]*>([^<]+)<', html)
        if m and m.group(1).strip():
            title = m.group(1).strip()
    if not title:
        m = re.search(r"<title>(.*?)</title>", html, re.S)
        if m:
            t = re.sub(r"\s+", " ", m.group(1)).strip()
            for pat in [r"_[^_]*的BLOG_新浪博客$", r"_[^_]*的博客_新浪博客$", r"_[^_]*_新浪博客$"]:
                t = re.sub(pat, "", t)
            if t and "新浪博客" not in t:
                title = t

    # ---- 时间 ----
    pub_time = ""
    d = re.search(r'class="time[^"]*"[^>]*>\(([^<]+)\)', html)
    if d:
        pub_time = d.group(1).strip()
    if not pub_time:
        d = re.search(r'(20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2}(?::\d{2})?)', html)
        if d:
            pub_time = d.group(1).strip()

    # ---- 正文（多套容器依次尝试） ----
    body = ""
    pats = [
        r'<div[^>]+class="articalContent[^"]*"[^>]*>(.*?)<!-- 正文结束 -->',
        r'<div[^>]+class="articalContent[^"]*"[^>]*>(.*?)(?=<!--\s*正文结束|</div>\s*<div[^>]+class="articalTag)',
        r'<div[^>]+class="articleContent[^"]*"[^>]*>(.*?)</div>\s*(?:<div|<!--)',
        r'<div[^>]+id="articleBody"[^>]*>(.*?)</div>',
        r'<div[^>]+class="postcontent[^"]*"[^>]*>(.*?)</div>',
        r'<div[^>]+class="articleContext[^"]*"[^>]*>(.*?)</div>',
    ]
    for p in pats:
        c = re.search(p, html, re.DOTALL)
        if not c:
            continue
        b = c.group(1)
        b = re.sub(r"<script.*?</script>", " ", b, flags=re.DOTALL)
        b = re.sub(r"<style.*?</style>", " ", b, flags=re.DOTALL)
        b = re.sub(r"<[^>]+>", " ", b)
        b = b.replace("&nbsp;", " ").replace("&#8203;", "").replace("&amp;", "&")
        b = re.sub(r"\s+", " ", b).strip()
        if len(b) > len(body):
            body = b
        if len(body) > 60:
            break

    # ---- 质量过滤：剔除导航碎片 / 无意义短文本 ----
    if body:
        # 纯导航特征：整段只有"前一篇/后一篇"链接文字
        nav = re.sub(r"(前一篇|后一篇|��一篇|下一篇)[:：]\s*", "", body)
        if re.search(r"前一篇|后一篇|上一篇|下一篇", body) and len(body) < 120:
            body = ""
        elif re.match(r"^[\s\S]{0,10}$", nav.strip()):
            body = ""
    if len(body) < 30:   # 正文过短视为无效
        body = ""

    return {"title": title, "pub_time": pub_time, "content": body}


def process(item, delay):
    aid, v = item
    fp = os.path.join(POSTS_DIR, aid + ".md")
    if os.path.exists(fp) and os.path.getsize(fp) > 0:
        with _lock:
            _stats["skip"] += 1
        return None
    if aid in _failed_ids:
        with _lock:
            _stats["skip"] += 1
        return None

    html = fetch_snapshot(v["timestamp"], v["url"])
    p = parse_article(html) if html else None
    if not p or not p["content"]:
        with _lock:
            _stats["fail"] += 1
            _failed_ids.add(aid)
        return {"article_id": aid, "url": v["url"], "reason": "无内容"}
    with io.open(fp, "w", encoding="utf-8") as f:
        f.write("---\narticle_id: %s\n---\n\n%s\n" % (aid, p["content"]))
    with _lock:
        _stats["ok"] += 1
        n = _stats["ok"]
    if n % 25 == 0:
        log("  已抓 %d 篇（失败 %d，跳过 %d）" % (n, _stats["fail"], _stats["skip"]))
    if delay:
        time.sleep(delay)
    return {"article_id": aid, "title": p["title"], "pub_time": p["pub_time"],
            "source_url": v["url"], "snapshot": v["timestamp"], "chars": len(p["content"])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--delay", type=float, default=0.0)
    ap.add_argument("--seconds", type=float, default=780)
    ap.add_argument("--test", type=int, default=0)
    args = ap.parse_args()

    for d in (OUT_DIR, POSTS_DIR):
        if not os.path.isdir(d):
            os.makedirs(d)

    # 说明：不再加载历史失败记录 —— SSL/通道类错误是临时性的，
    #       每次运行都应重新尝试；已成功的通过 .md 是否存在来判断跳过。
    _failed_ids.clear()

    data = json.load(io.open(LIST_FILE, encoding="utf-8"))
    items = list(data["items"].items())
    if args.test:
        items = items[:args.test]

    # 过滤掉已完成与已知失败的，减少无用请求
    todo = []
    for aid, v in items:
        if os.path.exists(os.path.join(POSTS_DIR, aid + ".md")):
            continue
        if aid in _failed_ids:
            continue
        todo.append((aid, v))

    log("清单 %d 篇；待抓 %d 篇（已知失败 %d，已抓 %d）"
        % (len(items), len(todo), len(_failed_ids),
           len([f for f in os.listdir(POSTS_DIR) if f.endswith('.md')])))

    stop_at = time.time() + args.seconds
    results, failed = [], []
    t0 = time.time()
    BATCH = max(1, args.workers * 6)
    idx = 0
    while idx < len(todo):
        if time.time() > stop_at:
            log("达到时限，停止提交")
            break
        batch = todo[idx: idx + BATCH]
        idx += len(batch)
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            for r in ex.map(lambda x: process(x, args.delay), batch):
                if r is None:
                    continue
                if "reason" in r:
                    failed.append(r)
                else:
                    results.append(r)

    if results or failed:
        old = []
        if os.path.exists(META_FILE):
            try:
                old = json.load(io.open(META_FILE, encoding="utf-8")).get("posts", [])
            except Exception:
                old = []
        with io.open(META_FILE, "w", encoding="utf-8") as f:
            json.dump({"source": "web.archive.org via public reverse proxies",
                       "total": len(old) + len(results), "posts": old + results},
                      f, ensure_ascii=False, indent=1)
        prev = []
        if os.path.exists(FAIL_FILE):
            try:
                prev = json.load(io.open(FAIL_FILE, encoding="utf-8"))
            except Exception:
                prev = []
        seen = set(x["article_id"] for x in prev)
        with io.open(FAIL_FILE, "w", encoding="utf-8") as f:
            json.dump(prev + [x for x in failed if x["article_id"] not in seen],
                      f, ensure_ascii=False, indent=1)

    el = time.time() - t0
    log("-" * 66)
    log("本轮：成功 %d，失败 %d，跳过 %d，耗时 %.0fs（%.3f 篇/秒）"
        % (_stats["ok"], _stats["fail"], _stats["skip"], el, _stats["ok"] / max(1, el)))
    total_done = len([f for f in os.listdir(POSTS_DIR) if f.endswith('.md')])
    log("累计已抓 %d / 13416 篇（%.1f%%）" % (total_done, total_done * 100.0 / 13416))


if __name__ == "__main__":
    main()