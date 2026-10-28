# -*- coding: utf-8 -*-
"""
按"每节点独立限速"的方式抓取 archive.org 快照（合规、避免限流）

核心思想：
    实测单个反代节点约 7 次/分钟即触发 429，
    因此给每个节点分配独立工作线程，各自串行 + 固定间隔（默认 9s），
    N 个节点即可获得约 N 倍的安全吞吐（4 节点 ≈ 26 请求/分钟）。

用法：
    python tools/wayback_multi.py --seconds 780 --gap 9
"""
import argparse
import io
import json
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
from queue import Queue

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIST_FILE = os.path.join(ROOT, "temp", "wayback_cdx2.json")
OUT_DIR = os.path.join(ROOT, "output", "1216826604_wayback")
POSTS_DIR = os.path.join(OUT_DIR, "posts")
FAIL_FILE = os.path.join(OUT_DIR, "failed.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36")

# 每个节点一对 (名称, 模板)；模板需支持 {raw} 或 {enc}
NODES = [
    ("fdgally",  "https://fd-gally.netlify.app/proxy/{raw}"),
    ("corshttp", "https://cors-http.netlify.app/.netlify/functions/api?url={enc}"),
    ("nocors",   "https://nocors.pages.dev/proxy?url={enc}"),
    ("allallow", "https://allallow.pages.dev/?url={enc}&mode=full"),
]

STATS = {"ok": 0, "fail": 0}
FAILED = []
LOCK = threading.Lock()
STOP = threading.Event()


def log(m):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), m))
    sys.stdout.flush()


# ---------- 解析（兼容新旧模板 + 质量过滤） ----------
def parse_article(html):
    if "设置了访问权限" in html or "该文章已经被加密" in html:
        return None

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

    pub_time = ""
    d = re.search(r'class="time[^"]*"[^>]*>\(([^<]+)\)', html)
    if d:
        pub_time = d.group(1).strip()
    if not pub_time:
        d = re.search(r'(20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2}(?::\d{2})?)', html)
        if d:
            pub_time = d.group(1).strip()

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

    # 导航碎片 / 过短内容过滤
    if body and re.search(r"前一篇|后一篇|上一篇|下一篇", body) and len(body) < 120:
        body = ""
    if len(body) < 30:
        body = ""
    return {"title": title, "pub_time": pub_time, "content": body}


def fetch(tmpl, timestamp, url, timeout=25):
    target = "https://web.archive.org/web/%s/%s" % (timestamp, url)
    enc = urllib.parse.quote(target, safe="")
    pu = tmpl.format(raw=target, enc=enc)
    req = urllib.request.Request(pu, headers={"User-Agent": UA,
                                              "Accept-Language": "zh-CN,zh;q=0.9"})
    raw = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")
    if not raw or "Temporarily Offline" in raw:
        return ""
    try:
        j = json.loads(raw)
        c = j.get("content", "")
        if c and len(c) > 2000:
            return c
        return ""
    except Exception:
        return raw if len(raw) > 2000 else ""


def worker(name, tmpl, q, gap):
    """每个节点一个线程：串行请求 + 固定间隔，避免触发该节点限流"""
    while not STOP.is_set():
        try:
            item = q.get_nowait()
        except Exception:
            return
        aid, v = item
        fp = os.path.join(POSTS_DIR, aid + ".md")
        if os.path.exists(fp) and os.path.getsize(fp) > 0:
            q.task_done()
            continue
        try:
            html = fetch(tmpl, v["timestamp"], v["url"])
            p = parse_article(html) if html else None
        except Exception as e:
            p = None
            with LOCK:
                FAILED.append({"article_id": aid, "url": v["url"],
                               "reason": "%s: %s" % (name, str(e)[:60])})
        if p and p["content"]:
            with io.open(fp, "w", encoding="utf-8") as f:
                f.write("---\narticle_id: %s\n---\n\n%s\n" % (aid, p["content"]))
            with LOCK:
                STATS["ok"] += 1
                n = STATS["ok"]
            if n % 20 == 0:
                log("  已抓 %d 篇（失败 %d） | %s" % (n, STATS["fail"], name))
        else:
            with LOCK:
                STATS["fail"] += 1
                if not any(x["article_id"] == aid for x in FAILED):
                    FAILED.append({"article_id": aid, "url": v["url"], "reason": "无有效正文"})
        q.task_done()
        if gap:
            time.sleep(gap)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gap", type=float, default=9.0, help="每节点请求间隔（秒）")
    ap.add_argument("--seconds", type=float, default=780)
    ap.add_argument("--nodes", type=int, default=0, help="只用前 N 个节点")
    args = ap.parse_args()

    for dd in (OUT_DIR, POSTS_DIR):
        if not os.path.isdir(dd):
            os.makedirs(dd)

    data = json.load(io.open(LIST_FILE, encoding="utf-8"))
    items = list(data["items"].items())
    todo = [(a, v) for a, v in items
            if not (os.path.exists(os.path.join(POSTS_DIR, a + ".md"))
                    and os.path.getsize(os.path.join(POSTS_DIR, a + ".md")) > 0)]
    log("清单 %d 篇；待抓 %d 篇；已抓 %d" % (len(items), len(todo),
                                          len(items) - len(todo)))

    nodes = NODES[:args.nodes] if args.nodes else NODES
    q = Queue()
    for it in todo:
        q.put(it)

    threads = []
    for name, tmpl in nodes:
        t = threading.Thread(target=worker, args=(name, tmpl, q, args.gap), daemon=True)
        t.start()
        threads.append(t)
        time.sleep(0.3)

    t0 = time.time()
    while time.time() - t0 < args.seconds:
        time.sleep(5)
        if q.empty():
            break
    STOP.set()
    time.sleep(2)

    el = time.time() - t0
    log("-" * 62)
    log("本轮：成功 %d，失败 %d，耗时 %.0fs（%.2f 篇/分钟）"
        % (STATS["ok"], STATS["fail"], el, STATS["ok"] / max(el / 60, 0.01)))
    total = len([f for f in os.listdir(POSTS_DIR) if f.endswith(".md")])
    log("累计已抓 %d / %d 篇（%.1f%%）" % (total, len(items), total * 100.0 / len(items)))

    with io.open(FAIL_FILE, "w", encoding="utf-8") as f:
        json.dump(FAILED, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()