# -*- coding: utf-8 -*-
"""
从"心明禅师"新浪博客补录 wu2198 的转载文章（175 篇）

背景：
    wu2198 原博客 uid=1216826604 的正文页已被权限拦截，但第三方博客
    "心明禅师"（uid=1302372530）设有一个 wu2198 专类，转载了 175 篇。

来源入口：
    列表：https://blog.sina.com.cn/s/articlelist_1302372530_2_{page}.html   （classid=2 即 wu2198 分类）
    正文：公开可访问，可正常解析 titName / time / articalContent

产物（与主数据分离存放，避免污染原博客数据集）：
    output/1216826604_supplement/posts/{article_id}.md
    output/1216826604_supplement/index.json
    output/1216826604_supplement/README.md

用法：
    python tools/fetch_xinming_supplement.py --max-pages 4 --delay 1.0
"""
import argparse
import io
import json
import os
import re
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.spider import SinaSpider          # noqa: E402  复用解析正则

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36")

SRC_UID = "1302372530"
SRC_NAME = "心明禅师"
TARGET_UID = "1216826604"
LIST_TMPL = "https://blog.sina.com.cn/s/articlelist_%s_2_%d.html" % (SRC_UID, 0)

OUT_DIR = os.path.join(ROOT, "output", "1216826604_supplement")
POSTS_DIR = os.path.join(OUT_DIR, "posts")
INDEX_FILE = os.path.join(OUT_DIR, "index.json")
TODO_FILE = os.path.join(ROOT, "temp", "wu2198_todo.json")

_pat_title = re.compile(r'<span class="atc_title">\s*<a[^>]+href="([^"]+)"[^>]*>([^<]*)</a>', re.S)
_pat_time = re.compile(r'<span class="atc_tm[^"]*">([^<]*)</span>')
_pat_orig_time = re.compile(r"\((\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\)")

_parser = SinaSpider.__new__(SinaSpider)


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg))
    sys.stdout.flush()


def fetch(url):
    for i in range(3):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Referer": "https://blog.sina.com.cn/",
                "Accept-Language": "zh-CN,zh;q=0.9"})
            return urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
        except Exception:
            time.sleep(1.5 * (i + 1))
    return ""


def collect_list(max_pages, delay):
    """抓取 wu2198 分类的分页列表"""
    rows, seen = [], set()
    for p in range(1, max_pages + 1):
        url = "https://blog.sina.com.cn/s/articlelist_%s_2_%d.html" % (SRC_UID, p)
        h = fetch(url)
        titles = _pat_title.findall(h)
        times = _pat_time.findall(h)
        log("第 %d 页: %d 条 (HTML %d 字节)" % (p, len(titles), len(h)))
        for i, (href, title) in enumerate(titles):
            u = href.replace("//blog.sina.com.cn", "https://blog.sina.com.cn")
            if u in seen:
                continue
            seen.add(u)
            m = re.search(r"blog_([^.]+)\.html", u)
            rows.append({
                "article_id": m.group(1) if m else "",
                "url": u,
                "list_title": title.strip(),
                "list_date": times[i].strip() if i < len(times) else "",
            })
        time.sleep(delay)
    return rows


def download(rows, delay, retry_empty=True):
    if not os.path.isdir(POSTS_DIR):
        os.makedirs(POSTS_DIR)
    todo = set()
    if os.path.exists(TODO_FILE):
        d = json.load(io.open(TODO_FILE, encoding="utf-8"))
        for x in d["items"]:
            t = re.sub(r"[\s，。！？、：；《》()（）\-—…,.!?\"'“”]", "", x["list_title"])
            todo.add(t)

    index = []
    ok = skip = fail = 0
    for n, row in enumerate(rows, 1):
        aid = row["article_id"]
        fp = os.path.join(POSTS_DIR, aid + ".md")
        if os.path.exists(fp) and os.path.getsize(fp) > 0:
            skip += 1
            continue

        h = fetch(row["url"])
        if not h:
            fail += 1
            log("  [FAIL] %s 请求失败" % row["url"])
            continue

        parsed = _parser.parse_article(h, row["url"])
        body = (parsed or {}).get("content", "")
        title = (parsed or {}).get("title") or row["list_title"]
        pub_time = (parsed or {}).get("pub_time") or row["list_date"]

        # 正文里常带原文发布时间，抽出来备用
        om = _pat_orig_time.search(body[:200])
        orig_time = om.group(1) if om else ""

        # 标题是否命中本地缺失清单
        clean = re.sub(r"[\s，。！？、：；《》()（）\-—…,.!?\"'“”]", "",
                       re.sub(r"^转[-\s]*wu\d*[-\s]*", "", title))
        matched = any(clean and len(clean) >= 6 and (clean in t or t in clean) for t in todo)

        with io.open(fp, "w", encoding="utf-8") as f:
            f.write("---\narticle_id: %s\n---\n\n%s\n" % (aid, body))

        index.append({
            "article_id": aid,
            "title": title,
            "pub_time": pub_time,
            "orig_pub_time": orig_time,
            "source_url": row["url"],
            "source_blog_uid": SRC_UID,
            "source_blog_name": SRC_NAME,
            "content_chars": len(body),
            "likely_supplements_todo": matched,
        })
        ok += 1
        if ok % 20 == 0:
            log("  已抓 %d 篇（跳过 %d，失败 %d）" % (ok, skip, fail))
        time.sleep(delay)

    with io.open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "version": "1.0",
            "source": {"uid": SRC_UID, "name": SRC_NAME, "classid": 2},
            "target": {"uid": TARGET_UID, "name": "wu2198"},
            "note": "第三方博客转载的 wu2198 文章，用于补充原博客当前无法访问的正文",
            "total": len(index),
            "posts": index,
        }, f, ensure_ascii=False, indent=1)

    log("-" * 60)
    log("新增 %d | 跳过 %d | 失败 %d | 索引 %d 篇" % (ok, skip, fail, len(index)))
    hit = sum(1 for x in index if x["likely_supplements_todo"])
    log("其中标题命中本地缺失清单的: %d 篇" % hit)
    return index


def write_readme(index):
    p = os.path.join(OUT_DIR, "README.md")
    with io.open(p, "w", encoding="utf-8") as f:
        f.write("# wu2198 转载补录数据（来源：心明禅师博客）\n\n")
        f.write("## 说明\n\n")
        f.write("wu2198 原博客（uid 1216826604）的正文页自 2026-10-10 下午起被权限拦截，"
                "无法继续获取正文。本目录收录第三方博客「心明禅师」（uid 1302372530）"
                "开设的 wu2198 专类所转载的文章，用于补充原站当前不可访问的内容。\n\n")
        f.write("- 来源列表：`https://blog.sina.com.cn/s/articlelist_1302372530_2_{page}.html`\n")
        f.write("- 采集时间：%s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
        f.write("- 篇数：%d\n\n" % len(index))
        f.write("## 注意\n\n")
        f.write("1. 这些是**第三方转载版本**，标题可能被编辑（多以 `转--` 开头），"
                "正文可能与原文有出入，**不建议直接用于正式知识库**，建议作为线索与补充。\n")
        f.write("2. 部分文章正文开头保留了原文的发布时间，已记入索引的 `orig_pub_time` 字段。\n")
        f.write("3. 索引中 `likely_supplements_todo = true` 的条目，标题与本地缺失清单吻合，"
                "可能补上了原博客未抓到的文章。\n")
        f.write("\n## 文件\n\n- `posts/{article_id}.md`：正文\n- `index.json`：索引\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-pages", type=int, default=4)
    ap.add_argument("--delay", type=float, default=1.0)
    args = ap.parse_args()

    if not os.path.isdir(OUT_DIR):
        os.makedirs(OUT_DIR)
    rows = collect_list(args.max_pages, 0.5)
    log("列表合计: %d 条" % len(rows))
    index = download(rows, args.delay)
    write_readme(index)
    log("产物目录: %s" % OUT_DIR)


if __name__ == "__main__":
    main()