# -*- coding: utf-8 -*-
"""
串行轮换抓取器（唯一验证可行的高效模式）

原理：
    实测结论 —— 触发限流的不是"总速率"，而是"瞬时并发"。
    只要严格串行（任何时刻仅 1 个在途请求），并以 4~5 秒间隔
    在多个节点间轮换，就能长期稳定拿到数据（实测探测 50~80% 有效）。

    反面案例：4 线程并行、即使每线程间隔 15s，启动瞬间的 4 个并发
    也会立刻触发 429 —— 因此本脚本坚持单线程。

用法：
    python tools/wayback_serial.py --seconds 780 --gap 4.5
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
LIST_FILE = os.path.join(ROOT, "temp", "wayback_cdx2.json")
OUT_DIR = os.path.join(ROOT, "output", "1216826604_wayback")
POSTS_DIR = os.path.join(OUT_DIR, "posts")
FAIL_FILE = os.path.join(OUT_DIR, "failed.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36")

NODES = [
    ("allallow", "https://allallow.pages.dev/?url={enc}&mode=full"),
    ("nocors",   "https://nocors.pages.dev/proxy?url={enc}"),
    ("fdgally",  "https://fd-gally.netlify.app/proxy/{raw}"),
    ("corshttp", "https://cors-http.netlify.app/.netlify/functions/api?url={enc}"),
]

STATS = {"ok": 0, "fail": 0, "net": 0}
FAILED = []


def log(m):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), m))
    sys.stdout.flush()


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

    if body and re.search(r"前一篇|后一篇|上一篇|下一篇", body) and len(body) < 120:
        body = ""
    if len(body) < 30:
        body = ""
    return {"title": title, "pub_time": pub_time, "content": body}


def fetch_one(name, tmpl, timestamp, url, timeout=25):
    """单次请求（无内部重试，避免叠加并发）"""
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
        return c if (c and len(c) > 2000) else ""
    except Exception:
        return raw if len(raw) > 2000 else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gap", type=float, default=4.5, help="两次请求间隔（秒）")
    ap.add_argument("--seconds", type=float, default=780)
    ap.add_argument("--max-fail", type=int, default=0,
                    help="连续失败达到该值则提前停止（0=不限制）")
    args = ap.parse_args()

    for dd in (OUT_DIR, POSTS_DIR):
        if not os.path.isdir(dd):
            os.makedirs(dd)

    data = json.load(io.open(LIST_FILE, encoding="utf-8"))
    items = list(data["items"].items())
    todo = [(a, v) for a, v in items
            if not (os.path.exists(os.path.join(POSTS_DIR, a + ".md"))
                    and os.path.getsize(os.path.join(POSTS_DIR, a + ".md")) > 0)]
    log("清单 %d 篇；待抓 %d 篇；已抓 %d" % (len(items), len(todo), len(items) - len(todo)))

    t0 = time.time()
    i = 0
    consec_fail = 0
    for aid, v in todo:
        if time.time() - t0 >= args.seconds:
            log("达到时限，停止")
            break
        name, tmpl = NODES[i % len(NODES)]
        i += 1
        fp = os.path.join(POSTS_DIR, aid + ".md")
        try:
            html = fetch_one(name, tmpl, v["timestamp"], v["url"])
            p = parse_article(html) if html else None
        except Exception as e:
            p = None
            STATS["net"] += 1
            if STATS["net"] <= 3:
                log("  网络异常(%s): %s" % (name, str(e)[:56]))
        if p and p["content"]:
            with io.open(fp, "w", encoding="utf-8") as f:
                f.write("---\narticle_id: %s\ntitle: %s\npub_time: %s\n---\n\n%s\n"
                        % (aid, p["title"], p["pub_time"], p["content"]))
            STATS["ok"] += 1
            consec_fail = 0
            if STATS["ok"] % 10 == 0:
                el = time.time() - t0
                log("  已抓 %d 篇（失败 %d / 网络异常 %d）| %.1f 篇/分钟"
                    % (STATS["ok"], STATS["fail"], STATS["net"], STATS["ok"] / max(el / 60, 0.01)))
        else:
            STATS["fail"] += 1
            consec_fail += 1
            FAILED.append({"article_id": aid, "url": v["url"], "node": name})
            if args.max_fail and consec_fail >= args.max_fail:
                log("  连续失败 %d 次，提前停止（可能被限流）" % consec_fail)
                break
        time.sleep(args.gap)

    el = time.time() - t0
    log("-" * 62)
    log("本轮：成功 %d，失败 %d，网络异常 %d，耗时 %.0fs（%.1f 篇/分钟）"
        % (STATS["ok"], STATS["fail"], STATS["net"], el, STATS["ok"] / max(el / 60, 0.01)))
    total = len([f for f in os.listdir(POSTS_DIR) if f.endswith(".md")])
    log("累计已抓 %d / %d 篇（%.1f%%）" % (total, len(items), total * 100.0 / len(items)))

    with io.open(FAIL_FILE, "w", encoding="utf-8") as f:
        json.dump(FAILED, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()