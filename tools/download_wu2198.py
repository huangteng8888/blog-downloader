# -*- coding: utf-8 -*-
"""
wu2198 (uid=1216826604) 专向采集器

背景：
    该博客主页 /u/{uid}、短链及 /s/articlelist_* 均被博主设置了访问权限，
    未登录访客会被跳转到权限提示页。但以下两个入口未被拦截，可正常抓取：
      - 列表：/s/article_sort_{uid}_10001_{page}.html
      - 正文：/s/blog_{article_id}.html

特性：
    - 第 1 页同样走 article_sort 接口（项目默认走主页，对本博客会拿到空列表）
    - 断点续跑：以 posts/*.md 是否存在为断点，重复运行自动跳过已抓
    - 有限并发 + 限速 + 失败记录 + 单次运行时长上限（配合外部超时控制分批执行）
    - 存储格式与项目完全一致：
        output/{uid}/posts/{article_id}.md   (frontmatter 仅 article_id)
        output/{uid}/posts/index.json
        output/{uid}/metadata.json

用法：
    python tools/download_wu2198.py --list-only --refresh-list   # 只抓列表并缓存
    python tools/download_wu2198.py --max-runtime 780            # 抓正文，到时限自动退出
    python tools/download_wu2198.py --max-runtime 780 --retry-failed
"""
import argparse
import io
import json
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.spider import SinaSpider          # noqa: E402  复用其 parse_article 正则
from src.storage import BlogStorage        # noqa: E402
from src.metadata import BlogMetadata      # noqa: E402

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36")

LIST_TMPL = "https://blog.sina.com.cn/s/article_sort_{uid}_10001_{page}.html"

# 与 src/spider.js 的 div.blog_title_h 解析思路一致，额外带出列表页日期
LIST_PAT = re.compile(
    r'<div class="blog_title"\s+id="t_10001_[^"]*"><a[^>]+href="([^"]+)">([^<]*)</a></div>'
    r'\s*<span class="time SG_txtc">\(([^)]*)\)'
)

ID_PAT = re.compile(r"blog_([^.]+)\.html")


def now_str():
    return time.strftime("%Y-%m-%d %H:%M:%S")


class Collector(object):
    def __init__(self, uid, name, output_root, delay, workers):
        self.uid = uid
        self.name = name
        self.delay = delay
        self.workers = max(1, workers)
        self.output_dir = os.path.join(ROOT, output_root, uid)
        self.posts_dir = os.path.join(self.output_dir, "posts")
        self.temp_dir = os.path.join(ROOT, "temp")
        self.list_cache = os.path.join(self.temp_dir, "wu2198_list.json")
        self.failed_file = os.path.join(self.temp_dir, "wu2198_failed.json")
        self.log_file = os.path.join(self.temp_dir, "wu2198_download.log")
        self._parser = SinaSpider.__new__(SinaSpider)  # 仅复用解析逻辑，不触发 __init__

        for d in (self.posts_dir, self.temp_dir):
            if not os.path.isdir(d):
                os.makedirs(d)

    # ---------- 基础 I/O ----------
    def log(self, msg):
        line = "[%s] %s" % (now_str(), msg)
        print(line)
        with io.open(self.log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    @staticmethod
    def fetch(url, ajax=False, retries=3):
        headers = {
            "User-Agent": UA,
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://blog.sina.com.cn/",
        }
        if ajax:
            headers["X-Requested-With"] = "XMLHttpRequest"
        last = None
        for i in range(retries):
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=30) as r:
                    return r.read().decode("utf-8", "ignore")
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(1.5 * (i + 1))
        raise last

    # ---------- 列表 ----------
    @staticmethod
    def parse_list(html):
        out, seen = [], set()
        for href, title, date in LIST_PAT.findall(html):
            url = href.replace("//blog.sina.com.cn", "https://blog.sina.com.cn")
            if url in seen:
                continue
            seen.add(url)
            out.append({
                "url": url,
                "list_title": title.strip(),
                "list_date": date.strip(),
            })
        return out

    def build_list(self, max_page=400, empty_streak_limit=3):
        """抓取全部分页列表并缓存到 temp/wu2198_list.json"""
        self.log("开始抓取列表分页 ...")
        items, seen = [], set()
        empty_streak = 0
        last_page = 0
        for page in range(1, max_page + 1):
            url = LIST_TMPL.format(uid=self.uid, page=page)
            try:
                html = self.fetch(url, ajax=True)
                page_items = self.parse_list(html)
            except Exception as e:  # noqa: BLE001
                self.log("  列表第 %d 页请求失败: %s" % (page, e))
                page_items = []

            if not page_items:
                empty_streak += 1
                if empty_streak >= empty_streak_limit:
                    self.log("  列表结束：连续 %d 页为空，最后一页=%d" % (empty_streak, last_page))
                    break
            else:
                empty_streak = 0
                last_page = page
                for it in page_items:
                    if it["url"] not in seen:
                        seen.add(it["url"])
                        items.append(it)
                if page % 20 == 0:
                    self.log("  第 %d 页 ... 累计 %d 条" % (page, len(items)))

            time.sleep(0.5)

        data = {
            "uid": self.uid,
            "crawled_at": now_str(),
            "pages": last_page,
            "total": len(items),
            "items": items,
        }
        with io.open(self.list_cache, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False, indent=2))
        self.log("列表缓存完成：%d 页 / %d 条 -> %s" % (last_page, len(items), self.list_cache))
        return items

    def load_list(self, refresh=False):
        if (not refresh) and os.path.exists(self.list_cache):
            with io.open(self.list_cache, encoding="utf-8") as f:
                data = json.load(f)
            self.log("读取列表缓存：%d 页 / %d 条（抓取于 %s）"
                     % (data.get("pages", 0), data.get("total", 0), data.get("crawled_at", "?")))
            return data["items"]
        return self.build_list()

    # ---------- 进度 / 失败 ----------
    def existing_ids(self):
        ids = set()
        for name in os.listdir(self.posts_dir):
            if name.endswith(".md"):
                ids.add(name[:-3])
        return ids

    def load_failed(self):
        if os.path.exists(self.failed_file):
            try:
                with io.open(self.failed_file, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:  # noqa: BLE001
                pass
        return {}

    def save_failed(self, failed):
        with io.open(self.failed_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(failed, ensure_ascii=False, indent=2))

    # ---------- 正文 ----------
    def _fetch_parse(self, it):
        """worker：抓取并解析单篇，返回 (item, parsed, err)"""
        url = it["url"]
        err = ""
        parsed = None
        try:
            html = self.fetch(url)
            parsed = self._parser.parse_article(html, url)
        except Exception as e:  # noqa: BLE001
            err = str(e)
        finally:
            time.sleep(self.delay)
        return it, parsed, err

    def download(self, items, max_runtime, retry_failed, allow_empty=False):
        started = time.time()
        meta = BlogMetadata(Path(self.output_dir))
        meta.init_blogger(uid=self.uid, name=self.name)
        meta.init_posts_index()
        storage = BlogStorage(self.posts_dir)

        done_ids = self.existing_ids()
        failed = {} if retry_failed else self.load_failed()
        if retry_failed:
            self.log("已清空历史失败记录，本次将重试全部失败项")

        pending = []
        list_ids = set()
        for it in items:
            m = ID_PAT.search(it["url"])
            aid = m.group(1) if m else ""
            if aid:
                list_ids.add(aid)
            if aid and aid in done_ids:
                continue
            if it["url"] in failed:
                continue
            pending.append(it)

        self.log("列表共 %d 条 | 本地已有 %d 篇 | 历史失败 %d 条 | 本次待抓 %d 条 | 并发 %d"
                 % (len(items), len(done_ids), len(failed), len(pending), self.workers))

        ok = fail = 0
        idx = 0
        chunk_size = max(1, self.workers * 2)
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            while idx < len(pending):
                if time.time() - started > max_runtime:
                    self.log("达到单次运行时长上限（%.0fs），保存进度后退出" % max_runtime)
                    break
                chunk = pending[idx: idx + chunk_size]
                idx += len(chunk)
                futures = [ex.submit(self._fetch_parse, it) for it in chunk]
                for fut in futures:
                    try:
                        it, parsed, err = fut.result()
                    except Exception as e:  # noqa: BLE001
                        self.log("  [WORKER-ERR] %s" % e)
                        continue
                    url = it["url"]
                    has_body = bool(parsed and parsed.get("content"))
                    has_meta = bool(parsed and (parsed.get("title") or it["list_title"]))
                    # 纯图片博文：标题/时间/图片有效但正文文字为空，源站内容即如此
                    if not has_meta or (not has_body and not allow_empty):
                        fail += 1
                        failed[url] = {
                            "error": err or "empty content",
                            "time": now_str(),
                            "title": it["list_title"],
                        }
                        self.log("  [FAIL] %s | %s | %s"
                                 % (ID_PAT.search(url).group(1) if ID_PAT.search(url) else "?",
                                    it["list_title"][:28], err or "empty"))
                        continue
                    post = {
                        "article_id": parsed.get("article_id") or "",
                        "author_uid": self.uid,
                        "author_name": self.name,
                        "pub_time": parsed.get("pub_time") or it["list_date"],
                        "source_url": url,
                        "title": parsed.get("title") or it["list_title"],
                        "tags": parsed.get("tags", []),
                        "image_urls": parsed.get("image_urls", []),
                    }
                    filepath = storage.save_post(post, parsed["content"])
                    post["filename"] = os.path.basename(str(filepath))
                    meta.add_post(post)
                    ok += 1
                    done_ids.add(post["article_id"])
                    if ok % 50 == 0:
                        meta.save()
                        self.save_failed(failed)
                        self.log("  已抓 %d 篇（失败 %d），最近：%s" % (ok, fail, post["title"][:28]))

        meta.save()
        self.save_failed(failed)

        stats = meta.generate_statistics()
        remain = len(list_ids - done_ids)
        self.log("-" * 60)
        self.log("本次新增 %d 篇 | 失败 %d | 耗时 %.0fs" % (ok, fail, time.time() - started))
        self.log("本地累计 %d 篇 | 索引 %d 篇 | 列表 %d 条 | 未完成 %d 条"
                 % (len(done_ids), stats["total_posts"], len(items), remain))
        self.log("时间范围：%s ~ %s" % (stats["date_range"]["earliest"], stats["date_range"]["latest"]))
        self.log("STATUS: %s" % ("COMPLETE" if remain <= 0 else "PARTIAL"))
        return remain


def main():
    ap = argparse.ArgumentParser(description="wu2198 新浪博客专向采集器")
    ap.add_argument("--uid", default="1216826604")
    ap.add_argument("--name", default="wu2198")
    ap.add_argument("--output", default="output")
    ap.add_argument("--delay", type=float, default=1.0, help="每个工作线程每次请求后的间隔秒数")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--max-runtime", type=float, default=780)
    ap.add_argument("--refresh-list", action="store_true")
    ap.add_argument("--list-only", action="store_true")
    ap.add_argument("--retry-failed", action="store_true")
    ap.add_argument("--allow-empty-content", action="store_true",
                    help="允许正文为空的纯图片博文入库（标题/时间/图片有效）")
    args = ap.parse_args()

    c = Collector(args.uid, args.name, args.output, args.delay, args.workers)
    items = c.load_list(refresh=args.refresh_list)
    if args.list_only:
        c.log("仅抓列表模式，结束。")
        return 0
    c.download(items, args.max_runtime, args.retry_failed, args.allow_empty_content)
    return 0


if __name__ == "__main__":
    sys.exit(main())