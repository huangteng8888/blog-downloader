#!/usr/bin/env python3
"""Fast article listing for 徐小明 using urllib + regex"""
import urllib.request
import urllib.error
import re
import json
import os
import time
from datetime import datetime

BASE = "/home/ht/github/blog-downloader"
UID = "1300871220"
INDEX = f"{BASE}/output/{UID}/posts/index.json"
DIR = f"{BASE}/output/{UID}/posts"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/121.0.0.0 Safari/537.36",
    "Referer": "https://blog.sina.com.cn/",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

def fetch(url):
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"[徐小明] Fetch error: {e}", file=__import__('sys').stderr)
        return ""

def parse_articles(html):
    # href="//blog.sina.com.cn/s/blog_ID.html">Title
    articles = []
    for m in re.finditer(r'href="//blog\.sina\.com\.cn(/s/blog_[0-9a-z]+\.html)"[^>]*>([^<]+)<', html):
        url = "https://blog.sina.com.cn" + m.group(1)
        title = m.group(2).strip()
        if title and len(title) > 2:
            articles.append({"url": url, "title": title})
    return articles

def find_last_page(html):
    pages = [int(p) for p in re.findall(r'page=(\d+)["&\'">]', html) if 0 < int(p) < 10000]
    return max(pages) if pages else 0

os.makedirs(DIR, exist_ok=True)

# Load existing
existing = {"total": 0, "updated_at": "", "posts": []}
if os.path.exists(INDEX):
    try:
        existing = json.load(open(INDEX))
    except: pass

existing_urls = {p["source_url"] for p in existing["posts"]}
all_posts = list(existing["posts"])

print(f"[徐小明] Existing: {len(all_posts)} posts")

# Fetch page 1
url1 = f"https://blog.sina.com.cn/s/articlelist_{UID}_0_1.html"
html = fetch(url1)
if "blog_4d89b8340103029i" not in html:
    print(f"[徐小明] Page 1 check failed, HTML length: {len(html)}")
    # Try to find what's happening
    if "captcha" in html.lower() or "验证" in html:
        print("[徐小明] CAPTCHA detected!")
    with open("/tmp/page1_debug.html", "w") as f:
        f.write(html[:2000])
    print("[徐小明] Wrote debug to /tmp/page1_debug.html")
    exit(1)

last_page = find_last_page(html)
print(f"[徐小明] Last page: {last_page}")

# Parse page 1
articles = parse_articles(html)
print(f"[徐小明] Page 1: {len(articles)} articles")

page = 1
while True:
    if page > 1:
        if page > last_page and last_page > 0:
            break
        url = f"https://blog.sina.com.cn/s/articlelist_{UID}_0_{page}.html"
        html = fetch(url)
        if not html or len(html) < 1000:
            print(f"[徐小明] Empty/short response on page {page}, stopping")
            break
        articles = parse_articles(html)
        print(f"[徐小明] Page {page}: {len(articles)} articles")

    new_count = 0
    for a in articles:
        if a["url"] not in existing_urls:
            blog_id = re.search(r"blog_([0-9a-z]+)\.html", a["url"]).group(1)
            all_posts.append({
                "id": blog_id,
                "title": a["title"],
                "published_at": "",
                "published_date": "",
                "tags": [],
                "images_count": 0,
                "filename": blog_id + ".md",
                "source_url": a["url"],
                "indexed_at": datetime.now().isoformat(),
            })
            existing_urls.add(a["url"])
            new_count += 1

    # Checkpoint
    if page % 10 == 0:
        with open(INDEX, "w") as f:
            json.dump({"total": len(all_posts), "updated_at": datetime.now().isoformat(), "posts": all_posts}, f, ensure_ascii=False, indent=2)
        print(f"[徐小明] Checkpoint page {page}: {len(all_posts)} posts ({new_count} new)")

    if len(articles) == 0:
        break
    if page >= last_page and last_page > 0:
        break

    page += 1
    time.sleep(0.5)

# Final save
with open(INDEX, "w") as f:
    json.dump({"total": len(all_posts), "updated_at": datetime.now().isoformat(), "posts": all_posts}, f, ensure_ascii=False, indent=2)

print(f"[徐小明] Done: {len(all_posts)} posts, {page} pages")
