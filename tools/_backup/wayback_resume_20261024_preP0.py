# -*- coding: utf-8 -*-
"""
wu2198 缺失文章补抓（archive.org 快照，严格低频）

设计要点（全部由 2026-10-24 实测得出）：
  1) 源站正文已被博主设为私有（详情页统一返回"你访问的博客设置了访问权限"），
     唯一通路是 archive.org 快照；本机无法直连 archive.org，必须经公开反代中转。
  2) 触发限流的是"瞬时并发"而非总速率 —— 因此任何时刻仅 1 个在途请求，
     默认间隔 15 秒（≈3.4 篇/分钟，实测 12 篇无一次 429）。
  3) 断点续跑：已存在且非空的 .md 直接跳过，随时中断随时重跑。
  4) 失败分类：权限/加密/无正文属"永久失败"，记入 skip 清单不再重试；
     网络异常/429 属"临时失败"，下次运行自动重试。
  5) 解析质量：HTML 实体解码（&#65292; → ，）、标题多级回退、
     正文模板多套匹配、导航碎片过滤、超长正文标记。

用法：
    python tools/wayback_resume.py --seconds 780 --gap 15
    python tools/wayback_resume.py --limit 20 --gap 15        # 只抓 20 篇
    python tools/wayback_resume.py --retry-skip               # 重试永久失败清单
"""
import argparse
import base64
import glob
import gzip
import hashlib
import html as htmllib
import io
import json
import os
import random
import re
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIST_FILE = os.path.join(ROOT, "temp", "wayback_cdx2.json")
OUT_DIR = os.path.join(ROOT, "output", "1216826604_wayback")
POSTS_DIR = os.path.join(OUT_DIR, "posts")
RAW_DIR = os.path.join(OUT_DIR, "raw")
SKIP_FILE = os.path.join(OUT_DIR, "skip_permanent.json")
FAIL_FILE = os.path.join(OUT_DIR, "failed_transient.json")
WARN_FILE = os.path.join(OUT_DIR, "warnings.json")
LOG_FILE = os.path.join(OUT_DIR, "progress.log")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36")

NODES = [
    ("allallow", "https://allallow.pages.dev/?url={enc}&mode=full"),
    ("nocors",   "https://nocors.pages.dev/proxy?url={enc}"),
    ("fdgally",  "https://fd-gally.netlify.app/proxy/{raw}"),
    ("corshttp", "https://cors-http.netlify.app/.netlify/functions/api?url={enc}"),
]

STATS = {"ok": 0, "photo": 0, "skip": 0, "transient": 0}
SKIPPED, TRANSIENT, WARNINGS = [], [], []


def log(m):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), m)
    print(line)
    sys.stdout.flush()
    try:
        with io.open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def clean_text(b):
    b = re.sub(r"<script.*?</script>", " ", b, flags=re.DOTALL | re.I)
    b = re.sub(r"<style.*?</style>", " ", b, flags=re.DOTALL | re.I)
    b = re.sub(r"<!--.*?-->", " ", b, flags=re.DOTALL)
    b = re.sub(r"<br\s*/?>", "\n", b, flags=re.I)
    b = re.sub(r"</p>", "\n", b, flags=re.I)
    b = re.sub(r"<[^>]+>", " ", b)
    b = htmllib.unescape(b)                     # &#65292; -> ，  &#9733; -> ★
    b = b.replace("\u200b", "").replace("\xa0", " ")
    b = re.sub(r"[ \t\u3000]+", " ", b)
    b = re.sub(r"\n\s*\n+", "\n", b)
    return b.strip()


NAV = re.compile(r"(前一篇|后一篇|上一篇|下一篇|返回列表|发表评论|阅读全文)")


def _cjk_ratio(t):
    if not t:
        return 0.0
    return sum(1 for c in t if "\u4e00" <= c <= "\u9fff") / float(len(t))


def fix_mojibake(t):
    """修复 GBK 字节被当 UTF-8 解出的乱码；对正常中文无副作用"""
    if not t:
        return t
    for src in ("gb18030", "gbk"):
        try:
            b = t.encode(src, "ignore")
        except Exception:
            continue
        r = b.decode("utf-8", "ignore")
        if not r or r == t:
            continue
        if _cjk_ratio(r) >= 0.25 or "新浪" in r or "博客" in r:
            if len(r) >= len(t) * 0.5 or "新浪" in r:
                return r
    return t


OPEN_PATS = [
    r'<div[^>]+class="articalContent[^"]*"[^>]*>',
    r'<div[^>]+id="articleContent"[^>]*>',
    r'<div[^>]+id="articleBody"[^>]*>',
    r'<div[^>]+class="articleContent[^"]*"[^>]*>',
    r'<div[^>]+class="postcontent[^"]*"[^>]*>',
    r'<div[^>]+id="sina_keyword_ad_area2"[^>]*>',
]

END_TERMS = [
    r'<!--\s*正文结束', r'<div[^>]+class="articalTag', r'<div[^>]+id="articleBottom',
    r'<div[^>]+class="blog_foot', r'<div[^>]+id="commentList', r'<div[^>]+class="articalInfo',
    r'<div[^>]+id="shareBox', r'<script[^>]+src="[^"]*(?:comment|ad_)[^"]*"',
]


def _balanced_end(html, start):
    depth, pos = 1, start
    for m in re.finditer(r"<(/?)div\b", html[start:], re.I):
        depth += -1 if m.group(1) else 1
        if depth == 0:
            return start + m.start()
        if m.start() > 90000:
            break
    return min(len(html), start + 90000)


def extract_body(html):
    """容器定位 + div 配平，避免浅层非贪婪截断"""
    best = ""
    for pat in OPEN_PATS:
        m = re.search(pat, html, re.I)
        if not m:
            continue
        start = m.end()
        seg = html[start:_balanced_end(html, start)]
        for t in END_TERMS:
            tm = re.search(t, seg, re.I)
            if tm:
                seg = seg[:tm.start()]
        b = clean_text(seg)
        if len(b) > len(best):
            best = b
        if len(best) > 80:
            break
    return fix_mojibake(best)


def parse_article(html):
    """返回 (status, data)；status ∈ ok / permission / encrypted / no_body"""
    if "设置了访问权限" in html or "你暂时不能查看" in html:
        return "permission", None
    if "该文章已经被加密" in html:
        return "encrypted", None

    title = ""
    for pat in [r'<h2[^>]+class="titName[^"]*"[^>]*>(.*?)</h2>',
                r'<a[^>]+id="articleTitle"[^>]*>(.*?)</a>',
                r'class="[^"]*articleTitle[^"]*"[^>]*>(.*?)<',
                r'<h1[^>]*class="[^"]*tit[^"]*"[^>]*>(.*?)</h1>']:
        m = re.search(pat, html, re.S)
        if m:
            t = clean_text(m.group(1))
            if t:
                title = t
                break
    if not title:
        m = re.search(r"<title>(.*?)</title>", html, re.S)
        if m:
            t = re.sub(r"\s+", " ", htmllib.unescape(m.group(1))).strip()
            for pat in [r"_[^_]{0,20}的BLOG_新浪博客$", r"_[^_]{0,20}的博客_新浪博客$",
                        r"_[^_]{0,20}_新浪博客$", r"_新浪博客$"]:
                t = re.sub(pat, "", t)
            if t and "新浪" not in t and "访问权限" not in t:
                title = t
    title = fix_mojibake(title)

    pub_time = ""
    for pat in [r'<span[^>]+id="articleTime"[^>]*>\s*([\d\-]{10,10}\s[\d:]{5,8})\s*</span>',
                r'class="time[^"]*"[^>]*>\(([^<]+)\)',
                r'class="time[^"]*"[^>]*>([^<]*20\d{2}[^<]*)<',
                r'(20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2}(?::\d{2})?)']:
        m = re.search(pat, html)
        if m:
            pub_time = m.group(1).strip()
            break

    body = extract_body(html)

    # 去掉正文里重复的标题行与 (日期) 行，再判断真实长度
    if body:
        lines = [l.strip() for l in body.split("\n") if l.strip()]
        if title:
            t_norm = re.sub(r"\s+", "", title)
            lines = [l for l in lines if re.sub(r"\s+", "", l) != t_norm]
        lines = [l for l in lines if not re.fullmatch(r"\(?\s*20\d{2}-\d{2}-\d{2}[^)]*\)?", l)]
        body = "\n".join(lines).strip()

    # 过滤纯导航碎片
    if body and len(body) < 120 and NAV.search(body):
        body = ""
    if len(body) < 30:
        # 源站即无正文（纯图片博文）——文章确实存在，标记类型后照常落盘
        if title:
            return "photo_only", {"title": title, "pub_time": pub_time, "content": ""}
        return "no_body", {"title": title, "pub_time": pub_time, "content": ""}
    return "ok", {"title": title, "pub_time": pub_time, "content": body}


def fetch_one(name, tmpl, timestamp, url, timeout=30):
    """返回 (文本, 通道原始字节, 是否被反代 JSON 包装)。

    第 2 项是逐字节保真证据：未被包装时其 SHA1(base32) 应与 CDX 索引 digest 完全一致，
    可用于事后字节级复核（见 tools/verify_digest.py）。
    """
    target = "https://web.archive.org/web/%s/%s" % (timestamp, url)
    enc = urllib.parse.quote(target, safe="")
    pu = tmpl.format(raw=target, enc=enc)
    req = urllib.request.Request(pu, headers={"User-Agent": UA,
                                              "Accept-Language": "zh-CN,zh;q=0.9"})
    delivered = urllib.request.urlopen(req, timeout=timeout).read()
    text = delivered.decode("utf-8", "ignore")
    if not text or "Temporarily Offline" in text:
        return "", delivered, False
    try:
        j = json.loads(text)
        if isinstance(j, dict) and "content" in j:
            return j.get("content") or "", delivered, True
        return text, delivered, False
    except Exception:
        return text, delivered, False


def b32_sha1(b):
    return base64.b32encode(hashlib.sha1(b).digest()).decode("ascii")


STRICT_REPOST = re.compile(
    r"(转载|转自|引自|引用自|摘自|文章来源|本文来源|内容来源|原文链接|原文出处)")


def detect_repost(content):
    """识别转载/引用痕迹，返回命中词（无则返回空串）。

    实测（2026-10-24，539 篇产物）：新浪旧模板会在正文末尾留下悬空的
    "文章引用自：" 标签（引用块内容已被剥离），若只看关键词会把 353 篇
    误判为转载。因此附加判据：标记之后必须仍有 >=30 字正文。
    """
    if not content:
        return ""
    head = content[:1500]
    for m in STRICT_REPOST.finditer(head):
        if len(head) - m.end() >= 30:
            return m.group(1)
    return ""


def save_raw(aid, ts, delivered):
    """归档通道原始字节（gzip）。失败不阻断主流程。"""
    if not delivered:
        return "", ""
    try:
        if not os.path.isdir(RAW_DIR):
            os.makedirs(RAW_DIR)
        fn = "%s.%s.html.gz" % (aid, ts)
        with gzip.open(os.path.join(RAW_DIR, fn), "wb", compresslevel=6) as f:
            f.write(delivered)
        return "raw/" + fn, b32_sha1(delivered)
    except Exception:
        return "", ""


def load_json(path, default):
    try:
        with io.open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gap", type=float, default=15.0, help="两次请求间隔秒数（默认 15，勿低于 10）")
    ap.add_argument("--seconds", type=float, default=780, help="本轮最长运行秒数")
    ap.add_argument("--limit", type=int, default=0, help="本轮最多抓取篇数（0=不限）")
    ap.add_argument("--max-fail", type=int, default=8, help="连续失败达到该值即提前停止（默认 8）")
    ap.add_argument("--retry-skip", action="store_true", help="连永久失败清单一起重试")
    ap.add_argument("--shuffle", action="store_true", help="随机顺序（默认按清单顺序）")
    args = ap.parse_args()

    for dd in (OUT_DIR, POSTS_DIR, RAW_DIR):
        if not os.path.isdir(dd):
            os.makedirs(dd)

    data = load_json(LIST_FILE, {}).get("items", {})
    if not data:
        log("清单为空：%s" % LIST_FILE)
        return

    done = {os.path.basename(f)[:-3] for f in glob.glob(os.path.join(POSTS_DIR, "*.md"))}
    skip = set() if args.retry_skip else set(load_json(SKIP_FILE, {}).keys())
    todo = [a for a in data if a not in done and a not in skip]
    if args.shuffle:
        random.seed(int(time.time()))
        random.shuffle(todo)

    total_list = len(data)
    log("清单 %d 篇 | 已抓 %d | 永久跳过 %d | 待抓 %d"
        % (total_list, len(done), len(skip), len(todo)))
    if not todo:
        log("无待抓条目，全部完成或已跳过")
        return

    t0 = time.time()
    i = 0
    consec = 0
    for n, aid in enumerate(todo):
        if time.time() - t0 >= args.seconds:
            log("达到本轮时限（%.0fs），停止" % args.seconds)
            break
        if args.limit and sum(STATS[k] for k in ("ok", "photo", "skip", "transient")) >= args.limit:
            log("达到本轮篇数上限（%d），停止" % args.limit)
            break
        v = data[aid]
        name, tmpl = NODES[i % len(NODES)]
        i += 1
        if n:
            time.sleep(args.gap + random.uniform(0, args.gap * 0.2))
        try:
            html, delivered, wrapped = fetch_one(name, tmpl, v["timestamp"], v["url"])
            status, p = parse_article(html) if html else ("no_body", None)
        except Exception as e:
            status, p = "transient", None
            msg = str(e)[:70]
            TRANSIENT.append({"article_id": aid, "node": name, "error": msg})
            STATS["transient"] += 1
            consec += 1
            log("  临时失败(%s) %s: %s" % (name, aid, msg))
            if consec >= args.max_fail:
                log("连续临时失败 %d 次，提前停止（疑似限流，稍后重跑即可续抓）" % consec)
                break
            continue

        if status in ("ok", "photo_only"):
            fp = os.path.join(POSTS_DIR, aid + ".md")
            rec = {
                "article_id": aid,
                "title": p["title"],
                "pub_time": p["pub_time"],
                "source": "archive.org snapshot",
                "snapshot_ts": v["timestamp"],
                "source_url": v["url"],
            }
            with io.open(fp, "w", encoding="utf-8") as f:
                f.write("---\n")
                for k in ("article_id", "title", "pub_time", "source", "snapshot_ts", "source_url"):
                    f.write("%s: %s\n" % (k, rec[k]))
                f.write("origin: wayback\n")
                f.write("stage: staged\n")
                raw_rel, raw_hash = save_raw(aid, v["timestamp"], delivered)
                if raw_rel:
                    f.write("raw_file: %s\n" % raw_rel)
                    f.write("raw_sha1_b32: %s\n" % raw_hash)
                    f.write("raw_wrapped: %d\n" % (1 if wrapped else 0))
                mark = detect_repost(p["content"])
                if mark:
                    f.write("repost: true\n")
                    f.write("repost_marker: %s\n" % mark)
                if status == "photo_only":
                    f.write("content_type: photo_only\n")
                f.write("---\n\n%s\n" % p["content"])
            if status == "photo_only":
                STATS["photo"] += 1
                log("  纯图片/无正文 %s《%s》" % (aid, p["title"][:24]))
            else:
                STATS["ok"] += 1
            consec = 0
            if len(p["content"]) > 30000:
                WARNINGS.append({"article_id": aid, "len": len(p["content"]),
                                 "note": "正文超长，需人工确认是否混入多篇"})
            if STATS["ok"] % 10 == 0:
                el = time.time() - t0
                log("  已抓 %d 篇（经 %.0fs，%.1f 篇/分钟）| 累计 %d/%d"
                    % (STATS["ok"], el, STATS["ok"] / max(el / 60, 0.01), len(done) + STATS["ok"], total_list))
        else:
            SKIPPED.append({"article_id": aid, "url": v["url"], "reason": status})
            STATS["skip"] += 1
            consec = 0
            log("  永久跳过(%s) %s" % (status, aid))

    el = time.time() - t0
    log("-" * 64)
    log("本轮：成功 %d | 纯图片 %d | 永久跳过 %d | 临时失败 %d | 耗时 %.0fs（%.1f 篇/分钟）"
        % (STATS["ok"], STATS["photo"], STATS["skip"], STATS["transient"], el,
           (STATS["ok"] + STATS["photo"]) / max(el / 60, 0.01)))

    # 合并落盘
    old_skip = load_json(SKIP_FILE, {})
    for s in SKIPPED:
        old_skip[s["article_id"]] = {"url": s["url"], "reason": s["reason"],
                                     "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    with io.open(SKIP_FILE, "w", encoding="utf-8") as f:
        json.dump(old_skip, f, ensure_ascii=False, indent=1)
    with io.open(FAIL_FILE, "w", encoding="utf-8") as f:
        json.dump(TRANSIENT, f, ensure_ascii=False, indent=1)
    with io.open(WARN_FILE, "w", encoding="utf-8") as f:
        json.dump(WARNINGS, f, ensure_ascii=False, indent=1)

    allmd = glob.glob(os.path.join(POSTS_DIR, "*.md"))
    log("累计已抓 %d / %d 篇（%.1f%%）| 永久跳过累计 %d 篇"
        % (len(allmd), total_list, len(allmd) * 100.0 / total_list, len(old_skip)))


if __name__ == "__main__":
    main()