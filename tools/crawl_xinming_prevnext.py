# -*- coding: utf-8 -*-
"""
通过「前一篇 / 后一篇」链并发遍历「心明禅师」博客，提取其转载的 wu2198 文章

为什么这样做：
    分类分页只有第 1 页可访问（第 2 页起返回空页），但每篇文章页底部都有
    「前一篇 / 后一篇」链接，构成贯穿全博客的链。以多个已知文章为起点、
    双向 + 多线程并发遍历，可绕过分页限制并大幅提速。

产物：
    output/1216826604_supplement/posts/{article_id}.md    wu2198 相关文章正文
    output/1216826604_supplement/index.json              索引（含原文发布时间）
    temp/xinming_walk_state.json                         断点
    temp/xinming_walk.log                                日志

用法：
    python tools/crawl_xinming_prevnext.py --seconds 780 --workers 8 --delay 0.3
"""
import argparse
import io
import json
import os
import re
import sys
import threading
import time
import urllib.request
from collections import deque

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.spider import SinaSpider          # noqa: E402

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36")

OUT_DIR = os.path.join(ROOT, "output", "1216826604_supplement")
POSTS_DIR = os.path.join(OUT_DIR, "posts")
INDEX_FILE = os.path.join(OUT_DIR, "index.json")
STATE_FILE = os.path.join(ROOT, "temp", "xinming_walk_state.json")
LOG_FILE = os.path.join(ROOT, "temp", "xinming_walk.log")

SRC_UID = "1302372530"
TARGET_UID = "1216826604"

RE_PREV = re.compile(r"前一篇：</span>\s*<a[^>]+href=\"([^\"]+)\"")
RE_NEXT = re.compile(r"后一篇：</span>\s*<a[^>]+href=\"([^\"]+)\"")
RE_CLASS = re.compile(r'class="blog_class"[^>]*>(.*?)</td>', re.S)
RE_CAT = re.compile(r'分类[:：]\s*</span>\s*<a[^>]*>([^<]*)<')
RE_ORIG = re.compile(r"\((\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?::\d{2})?)\)")

_parser = SinaSpider.__new__(SinaSpider)

LINKS_FILE = os.path.join(ROOT, "temp", "xinming_links.json")

_lock = threading.Lock()
_queue = deque()
_visited = set()
_inflight = 0
_found = {}
_retry = {}
_failed = set()
_links = {}
_stats = {"n": 0, "hit": 0}


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line)
    sys.stdout.flush()
    with io.open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def fetch(url):
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": UA, "Referer": "https://blog.sina.com.cn/",
            "Accept-Language": "zh-CN,zh;q=0.9"})
        return urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore")
    except Exception:
        return ""


def norm(href):
    return href.replace("//blog.sina.com.cn", "https://blog.sina.com.cn") if href else ""


def is_wu2198(title, body, cat):
    t = (title or "").lower()
    if "wu2198" in t or "wu先生" in (title or "") or "妩媚" in (title or "") or "村花" in (title or ""):
        return True
    if cat and ("2198" in cat or "wu" in cat.lower()):
        return True
    if body and ("wu2198" in body.lower() or "妩媚" in body):
        return True
    return False


def push(url):
    if not url:
        return
    if isinstance(url, dict):          # 兼容旧版断点格式 {"url":..., "dir":...}
        url = url.get("url", "")
    if not url:
        return
    with _lock:
        if url not in _visited:
            _queue.append(url)


def worker(stop_at, delay):
    global _inflight
    while True:
        if time.time() > stop_at:
            return
        with _lock:
            if not _queue:
                if _inflight == 0:
                    return
                url = None
            else:
                url = _queue.popleft()
                if url in _visited:
                    url = None
                else:
                    _visited.add(url)
                    _inflight += 1
        if url is None:
            time.sleep(0.15)
            continue

        try:
            html = fetch(url)
            if not html:
                # 抓取失败：重试若干次，避免断链导致遍历提前结束
                with _lock:
                    r = _retry.get(url, 0) + 1
                    _retry[url] = r
                    if r <= 3:
                        _queue.append(url)
                    else:
                        _failed.add(url)
                        _visited.discard(url)
                continue
            if html:
                parsed = _parser.parse_article(html, url) or {}
                title = parsed.get("title", "")
                body = parsed.get("content", "")
                pub_time = parsed.get("pub_time", "")
                m = re.search(r"blog_([^.]+)\.html", url)
                aid = m.group(1) if m else ""

                cat = ""
                mc = RE_CLASS.search(html)
                if mc:
                    cat = re.sub(r"<[^>]+>", " ", mc.group(1)).strip()
                else:
                    mc2 = RE_CAT.search(html)
                    if mc2:
                        cat = mc2.group(1).strip()

                om = RE_ORIG.search(body[:200])
                orig_time = om.group(1) if om else ""

                hit = is_wu2198(title, body, cat)
                if hit and body:
                    if not os.path.isdir(POSTS_DIR):
                        os.makedirs(POSTS_DIR)
                    fp = os.path.join(POSTS_DIR, aid + ".md")
                    if not os.path.exists(fp):
                        with io.open(fp, "w", encoding="utf-8") as f:
                            f.write("---\narticle_id: %s\n---\n\n%s\n" % (aid, body))
                    with _lock:
                        _found[aid] = {
                            "article_id": aid, "title": title, "pub_time": pub_time,
                            "orig_pub_time": orig_time, "source_url": url,
                            "category": cat, "content_chars": len(body),
                        }
                        _stats["hit"] = len(_found)

                with _lock:
                    _stats["n"] += 1
                    n = _stats["n"]
                if n % 50 == 0:
                    log("  已遍历 %d 篇，命中 wu2198 %d 篇" % (n, len(_found)))

                np = RE_NEXT.search(html)
                pp = RE_PREV.search(html)
                nu = norm(np.group(1)) if np else ""
                pu = norm(pp.group(1)) if pp else ""
                with _lock:
                    _links[url] = {"prev": pu, "next": nu}
                push(nu)
                push(pu)
        finally:
            with _lock:
                _inflight -= 1
            time.sleep(delay)


def save_state(stop_at):
    left = list(_queue)
    with io.open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "visited": sorted(_visited),
            "found": _found,
            "frontier": [u for u in left if u not in _visited],
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }, f, ensure_ascii=False, indent=1)
    with io.open(LINKS_FILE, "w", encoding="utf-8") as f:
        json.dump(_links, f, ensure_ascii=False, indent=1)


def write_index():
    items = sorted(_found.values(),
                   key=lambda x: (x.get("orig_pub_time") or x["pub_time"] or ""))
    with io.open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "version": "1.1",
            "source": {"uid": SRC_UID, "name": "心明禅师"},
            "target": {"uid": TARGET_UID, "name": "wu2198"},
            "note": ("第三方博客「心明禅师」转载的 wu2198 文章。"
                     "pub_time=转载发布时间；orig_pub_time=正文中保留的原文发布时间。"),
            "total": len(items),
            "posts": items,
        }, f, ensure_ascii=False, indent=1)


def load_state():
    global _visited, _found, _links
    if os.path.exists(LINKS_FILE):
        try:
            _links = json.load(io.open(LINKS_FILE, encoding="utf-8"))
        except Exception:
            _links = {}
    if not os.path.exists(STATE_FILE):
        return []
    d = json.load(io.open(STATE_FILE, encoding="utf-8"))
    _visited = set(d.get("visited", []))
    _found = d.get("found", {})
    return d.get("frontier", [])


def start_ids():
    ids = set()
    if os.path.isdir(POSTS_DIR):
        for f in os.listdir(POSTS_DIR):
            if f.endswith(".md"):
                ids.add(f[:-3])
    return sorted(ids)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=780)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--delay", type=float, default=0.3)
    args = ap.parse_args()

    if not os.path.isdir(OUT_DIR):
        os.makedirs(OUT_DIR)

    frontier = load_state()
    log("载入断点：已访问 %d 篇，已记录 wu2198 %d 篇，待续 %d 条"
        % (len(_visited), len(_found), len(frontier)))

    starts = start_ids()
    for s in starts:
        push("https://blog.sina.com.cn/s/blog_%s.html" % s)
    for u in frontier:
        push(u)
    # 边界补全：从已记录的 prev/next 关系中找出尚未访问的邻居
    edges = 0
    with _lock:
        for _src, lk in _links.items():
            for k in ("prev", "next"):
                u = lk.get(k)
                if u and u not in _visited and u not in _queue:
                    _queue.append(u)
                    edges += 1
    log("起点 %d 个 + 待续 %d 条 + 边界 %d 条 → 队列 %d"
        % (len(starts), len(frontier), edges, len(_queue)))

    stop_at = time.time() + args.seconds
    threads = [threading.Thread(target=worker, args=(stop_at, args.delay))
               for _ in range(args.workers)]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    save_state(stop_at)
    write_index()

    log("-" * 66)
    log("本轮遍历 %d 篇，耗时 %.0fs（%.1f 篇/秒）"
        % (_stats["n"], time.time() - t0, _stats["n"] / max(1, time.time() - t0)))
    log("累计：已访问 %d 篇，wu2198 相关 %d 篇" % (len(_visited), len(_found)))
    log("STATUS: %s" % ("COMPLETE" if not _queue else "PARTIAL（剩余 %d 条待续）" % len(_queue)))


if __name__ == "__main__":
    main()