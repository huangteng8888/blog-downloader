# -*- coding: utf-8 -*-
"""增量同步新浪博客最新文章（列表页 → 正文 → md + index.json）。

与 src/runner.py 的区别：
    1. 列表页直接用 curl 取 /s/articlelist_{uid}_0_{page}.html，不依赖 node；
       该入口每页 50 条，比首页 /u/{uid} 更稳（实测 2026-10-27）。
    2. 只在"遇到早于本地最新日期的整页"时停止，不靠"连续 N 页无新增"，
       因此能跨越本地断更期（徐小明 2026-06-10 → 至今）一次性补齐。
    3. 支持 --refresh-days：最近 N 天内的文章即使本地已存在也重新抓取，
       用于补全「盘中即时直播」这类当天持续追加正文的帖子。

安全约束：
    - 已有 md 若正文非空、而本次解析结果为空，则保留原文件（防覆盖为空白）；
    - 每轮首次写入前把 index.json 备份为 index.json.syncbak（不覆盖已有备份）。

用法：
    python tools/sync_latest.py --uid 1300871220 --name 徐小明 --dry-run
    python tools/sync_latest.py --uid 1300871220 --name 徐小明 --max-pages 12
"""
import argparse
import atexit
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from spider import SinaSpider              # noqa: E402
from storage import BlogStorage           # noqa: E402
from metadata import BlogMetadata         # noqa: E402

LIST_TPL = "https://blog.sina.com.cn/s/articlelist_{uid}_0_{page}.html"
RE_CELL = re.compile(r'<div class="articleCell', re.S)
RE_HREF = re.compile(r'<span class="atc_title">.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
RE_TM = re.compile(r'<span class="atc_tm[^"]*">\s*([^<\s][^<]*?)\s*</span>')

STOP_PAGES = 2          # 连续 N 页全部早于基线 → 停止翻页
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36")


def log(path, msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    # pythonw.exe 下 sys.stdout 为 None，直接 print 会抛 AttributeError
    if sys.stdout is not None:
        print(line)
        sys.stdout.flush()
    with io.open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def parse_list(html):
    """解析列表页，返回 [{article_id,title,url,pub_time}]（按页面顺序，新→旧）。"""
    out = []
    for chunk in RE_CELL.split(html)[1:]:
        m = RE_HREF.search(chunk)
        if not m:
            continue
        href = m.group(1).strip()
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        t = RE_TM.search(chunk)
        if not t:
            continue
        pub_time = t.group(1).strip()
        am = re.search(r"blog_([^.]+)\.html", href)
        if not am:
            continue
        if href.startswith("//"):
            href = "https:" + href
        out.append({
            "article_id": am.group(1),
            "title": title,
            "url": href,
            "pub_time": pub_time,
        })
    return out


def fetch_page(uid, page, tries=3):
    """抓取列表页（带重试 + 防缓存参数）。

    实测该入口偶发两种故障：返回上一页的重复内容、以及 curl 30s 超时。
    因此每次重试用递增的 _v 参数绕过缓存，并在解析为空时继续重试。
    """
    base = LIST_TPL.format(uid=uid, page=page)
    for k in range(tries):
        url = "%s?_v=%d" % (base, int(time.time() * 1000) % 1000000)
        tmp = tempfile.mktemp(suffix=".html")
        cmd = ["curl", "-s", "-L", "--max-time", "20", "-o", tmp, "-w", "%{http_code}",
               "-H", "User-Agent: " + UA,
               "-H", "Accept-Language: zh-CN,zh;q=0.9",
               "-H", "Referer: https://blog.sina.com.cn/", url]
        html = ""
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
            if os.path.exists(tmp):
                html = io.open(tmp, "r", encoding="utf-8", errors="ignore").read()
        except Exception:
            html = ""
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        rows = parse_list(html) if html else []
        if rows:
            return rows
        time.sleep(1.5 * (k + 1))
    return []


def _pid_alive(pid):
    """Windows 下安全探活。

    注意：不能用 os.kill(pid, 0)，Windows 下它会走 TerminateProcess 而真的杀掉进程。
    """
    try:
        r = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/FO", "CSV", "/NH"],
                           capture_output=True, text=True, timeout=15)
        return str(pid) in (r.stdout or "")
    except Exception:
        return False


def acquire_lock(path):
    """单实例锁。返回 (是否拿到, 占用者PID)。防止计划任务与手动运行撞车。"""
    if os.path.exists(path):
        try:
            pid = int(io.open(path, encoding="utf-8").read().strip() or 0)
        except Exception:
            pid = 0
        if pid and pid != os.getpid() and _pid_alive(pid):
            return False, pid
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    return True, 0


def fetch_article_retry(sp, url, tries=2):
    """正文抓取（带重试）。实测 curl 偏30s 超时时有发生，单次失败不应丢文。"""
    last = None
    for k in range(tries):
        try:
            html = sp.fetch_article(url)
            if html:
                return html
        except Exception as e:
            last = e
        time.sleep(2.5 * (k + 1))
    if last:
        raise last
    return ""


def load_local(uid):
    """从 index.json 读已有文章：{article_id: pub_time} + 最新日期 + 博主名。"""
    p = os.path.join(ROOT, "output", uid, "posts", "index.json")
    if not os.path.exists(p):
        return {}, "", ""
    d = json.load(io.open(p, encoding="utf-8"))
    mp = {}
    author = ""
    for it in d.get("posts", []):
        aid = it.get("article_id")
        if aid:
            mp[aid] = (it.get("pub_time") or "")[:19]
        if not author and it.get("author_name"):
            author = it["author_name"]
    latest = max([v[:10] for v in mp.values() if v] or [""])
    return mp, latest, author


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uid", required=True)
    ap.add_argument("--name", default="")
    ap.add_argument("--max-pages", type=int, default=12, help="最多翻多少页列表（每页 50 条）")
    ap.add_argument("--gap", type=float, default=2.0, help="单篇间隔秒（含 0-30%% 抖动）")
    ap.add_argument("--seconds", type=float, default=1800, help="本轮最长运行秒数")
    ap.add_argument("--refresh-days", type=int, default=2,
                    help="最近 N 天的已存在文章也重新抓（补全直播贴）")
    ap.add_argument("--since", default="", help="只同步该日期(YYYY-MM-DD)之后，默认=本地最新日期")
    ap.add_argument("--dry-run", action="store_true", help="只统计积压，不落盘")
    args = ap.parse_args()

    uid = args.uid
    out_dir = os.path.join(ROOT, "output", uid)
    posts_dir = os.path.join(out_dir, "posts")
    log_file = os.path.join(out_dir, "sync.log")
    warn_file = os.path.join(out_dir, "sync_warnings.json")

    os.makedirs(posts_dir, exist_ok=True)
    lock_file = os.path.join(out_dir, ".sync.lock")
    got, holder = acquire_lock(lock_file)
    if not got:
        log(log_file, "已有实例在运行（PID %d），本次退出" % holder)
        return 0
    atexit.register(lambda: os.path.exists(lock_file) and os.unlink(lock_file))

    local, latest, author = load_local(uid)
    # 博主名默认从索引继承，避免计划任务命令行携带中文
    name = args.name or author or uid
    base = args.since or latest
    if not base:
        log(log_file, "本地无基线日期，请用 --since 指定")
        return 2
    refresh_from = (date.today() - timedelta(days=args.refresh_days)).isoformat()

    log(log_file, "=" * 60)
    log(log_file, "同步 %s(%s)：本地 %d 篇，最新 %s，模式 %s"
        % (name, uid, len(local), latest,
           "DRY-RUN" if args.dry_run else "写入"))

    sp = SinaSpider(uid, output_dir=posts_dir)

    # ── 1. 翻列表页，收集基线之后的条目 ───────────────────────────
    todo, seen = [], set()
    stop_streak = 0
    prev_top = ""
    for page in range(1, args.max_pages + 1):
        rows = fetch_page(uid, page)
        if not rows:
            log(log_file, "第 %d 页重试后仍无条目 → 停止翻页" % page)
            break
        # 重复页检测：正常应严格按时间倒序，若本页最新一条不比上页更旧，则再取一次
        if prev_top and rows[0]["pub_time"] > prev_top:
            log(log_file, "第 %d 页疑似重复（%s > 上页 %s），延迟后重取"
                % (page, rows[0]["pub_time"], prev_top))
            time.sleep(3)
            rows2 = fetch_page(uid, page, tries=2)
            if rows2 and rows2[0]["pub_time"] <= prev_top:
                rows = rows2
            else:
                log(log_file, "第 %d 页仍为重复内容，跳过该页" % page)
                continue
        prev_top = prev_top or rows[0]["pub_time"]
        if rows[0]["pub_time"] < prev_top:
            prev_top = rows[0]["pub_time"]
        fresh = [r for r in rows if r["pub_time"][:10] >= base]
        older = len(rows) - len(fresh)
        log(log_file, "第 %d 页 %d 条：%s ~ %s，其中 ≥基线 %d 条"
            % (page, len(rows), rows[-1]["pub_time"][:10], rows[0]["pub_time"][:10], len(fresh)))
        for r in fresh:
            if r["article_id"] in seen:
                continue
            seen.add(r["article_id"])
            todo.append(r)
        stop_streak = stop_streak + 1 if older == len(rows) else 0
        if stop_streak >= STOP_PAGES:
            log(log_file, "连续 %d 页全部早于基线 → 停止翻页" % STOP_PAGES)
            break

    new = [r for r in todo if r["article_id"] not in local]
    ref = [r for r in todo if r["article_id"] in local
           and r["pub_time"][:10] >= refresh_from]
    log(log_file, "列表命中 %d 条：新增 %d 条，待刷新(近%d天) %d 条"
        % (len(todo), len(new), args.refresh_days, len(ref)))

    if not todo:
        log(log_file, "无可同步内容，退出")
        return 0
    if new:
        log(log_file, "新增最早 %s → 最晚 %s"
            % (min(r["pub_time"] for r in new), max(r["pub_time"] for r in new)))
    if args.dry_run:
        for r in new[:10]:
            log(log_file, "  [预览] %s %s" % (r["pub_time"], r["title"][:40]))
        if len(new) > 10:
            log(log_file, "  ... 其余 %d 条略" % (len(new) - 10))
        return 0

    # ── 2. 备份索引并逐篇抓取 ────────────────────────────────────
    idx_file = os.path.join(posts_dir, "index.json")
    bak = os.path.join(posts_dir, "index.json.syncbak")
    if not os.path.exists(bak) and os.path.exists(idx_file):
        with io.open(idx_file, "rb") as s, io.open(bak, "wb") as d:
            d.write(s.read())
        log(log_file, "已备份索引 → index.json.syncbak")

    storage = BlogStorage(posts_dir)
    meta = BlogMetadata(Path(out_dir))
    meta.init_posts_index()

    warnings = []
    if os.path.exists(warn_file):
        try:
            warnings = json.load(io.open(warn_file, encoding="utf-8"))
        except Exception:
            warnings = []

    t0 = time.time()
    n_ok = n_skip = n_fail = 0
    for i, r in enumerate(new + ref, 1):
        if time.time() - t0 > args.seconds:
            log(log_file, "到达时限 --seconds %.0f，剩余 %d 条留待下轮"
                % (args.seconds, len(new + ref) - i + 1))
            break

        aid = r["article_id"]
        fp = os.path.join(posts_dir, aid + ".md")
        is_refresh = aid in local
        try:
            html = fetch_article_retry(sp, r["url"])
            if not html:
                raise RuntimeError("空响应")
            p = sp.parse_article(html, r["url"])
            if not p:
                raise RuntimeError("解析失败")
            body = (p.get("content") or "").strip()
            old_body = ""
            if os.path.exists(fp):
                old_body = io.open(fp, encoding="utf-8", errors="ignore").read()

            if not body:
                if old_body and len(old_body) > 60:
                    n_skip += 1
                    warnings.append({"article_id": aid, "title": r["title"],
                                     "reason": "新解析为空，保留旧文件",
                                     "at": time.strftime("%Y-%m-%d %H:%M:%S")})
                    log(log_file, "  [保留旧文] %s %s" % (aid, r["title"][:32]))
                else:
                    n_skip += 1
                    warnings.append({"article_id": aid, "title": r["title"],
                                     "reason": "源站正文为空",
                                     "at": time.strftime("%Y-%m-%d %H:%M:%S")})
                    log(log_file, "  [空文跳过] %s %s" % (aid, r["title"][:32]))
            elif is_refresh and body == (old_body.split("---", 2)[-1].strip() if old_body else ""):
                n_skip += 1
            else:
                post = {
                    "article_id": aid,
                    "author_uid": uid,
                    "author_name": name,
                    "pub_time": p.get("pub_time") or r["pub_time"],
                    "source_url": r["url"],
                    "title": p.get("title") or r["title"],
                    "tags": p.get("tags") or [],
                    "image_urls": p.get("image_urls") or [],
                }
                filepath = storage.save_post(post, body)
                post["filename"] = filepath.name
                meta.add_post(post)
                n_ok += 1
                log(log_file, "[%d/%d] %s %s (%d字)%s"
                    % (i, len(new + ref), aid, post["title"][:30], len(body),
                       " [刷新]" if is_refresh else ""))
        except Exception as e:
            n_fail += 1
            log(log_file, "[%d/%d] 失败 %s：%s" % (i, len(new + ref), aid, e))

        time.sleep(args.gap * (1 + 0.3 * (os.urandom(1)[0] / 255.0)))

    meta.save()
    if warnings:
        json.dump(warnings, io.open(warn_file, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)

    log(log_file, "本轮完成：写入 %d，跳过 %d，失败 %d，耗时 %.0fs"
        % (n_ok, n_skip, n_fail, time.time() - t0))
    log(log_file, "本地现有 %d 篇（索引 %d 篇）"
        % (len([f for f in os.listdir(posts_dir) if f.endswith(".md")]), len(meta.posts)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
