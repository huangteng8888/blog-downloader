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
NODE_FILE = os.path.join(OUT_DIR, "node_stats.json")   # 节点观测（2026-10-25 新增）

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36")

# ---------------------------------------------------------------------------
# 中转节点白名单。轮询规则：NODES[i % len(NODES)]，逐篇轮换。
#
# 2026-10-25 13:10 人工决策（依据 80 次派发的实测计数，四家严格均等故可直比）：
#   保留 fdgally  成功 72/79 = 91%
#   保留 corshttp 成功 75/79 = 95%
#   移出 nocors   失败 77/80 =  96%（全部抛 HTTP 429），仅 3 次成功
#        —— 纯损耗；但它"诚实"：抛异常 → 记为临时失败 → 日后会重试，不损坏数据。
#   移出 allallow 跳过 77/80 =  96%（返回 429 错误页，HTTP 200 伪装），仅 3 次成功
#        —— 最危险：被旧解析器误判为 no_body → 写入永久跳过，把可恢复的文章静默判死。
#               本轮已修 parse_article 的错误页识别，但该节点本身已无可用性，故一并移出。
# 移出后仍为严格串行、间隔不变（15 秒/篇 + 0–20% 抖动），只是把 50% 的无效派发收回来。
# 定义保留在 DISABLED_NODES，便于日后复验或替换时直接启用。
# ---------------------------------------------------------------------------
NODES = [
    ("fdgally",  "https://fd-gally.netlify.app/proxy/{raw}"),
    ("corshttp", "https://cors-http.netlify.app/.netlify/functions/api?url={enc}"),
]

DISABLED_NODES = [
    ("allallow", "https://allallow.pages.dev/?url={enc}&mode=full"),
    ("nocors",   "https://nocors.pages.dev/proxy?url={enc}"),
]

STATS = {"ok": 0, "photo": 0, "skip": 0, "transient": 0}
SKIPPED, TRANSIENT, WARNINGS = [], [], []

# 节点观测（2026-10-25 新增）：**只统计，不改任何抓取行为**。
# 动机：failed_transient.json 只记录了"失败条目"的节点，缺"各节点被派了多少请求"这个分母，
# 于是"429 全集中在 nocors"无法与"nocors 只是流量更大"区分开。而节点是 i % len(NODES)
# 严格轮询、理论上四家均等，所以补上分母就能直接比较各家成功率——不必去动节点策略。
# 键含义：try=派发次数、ok/photo/skip=成功类结果、fail=临时失败、
#         err429=其中 HTTP 429、errnet=其中网络类、err_other=其余、last=最近一次时间。
NODE_KEYS = ("try", "ok", "photo", "skip", "fail", "err429", "errnet", "err_other")
NODE_STATS = {}


def log(m):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), m)
    print(line)
    sys.stdout.flush()
    try:
        with io.open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


PARSE_REV = "p0-20261025"   # 解析器修订号，写入正文 frontmatter 便于追溯
# 2026-10-25 修订要点：新增反代/站点错误页识别（429、5xx）。此前 allallow 把 429 错误页
# 以 HTTP 200 返回、正文仅 117 字节，旧逻辑取不到标题与正文 → 判 no_body → 写入永久跳过。
# 同日追加（第二轮核查）：
#   a) Wayback「无正文壳页」识别 —— 仅含播放器脚本、无 <body> 的约 1.9KB 响应；
#      只有壳页内确证跳转站点错误页才判 no_body（永久），否则判 proxy_error（临时失败），
#      堵住"可恢复文章被静默判死"的路径；
#   b) unescape_archived() —— 还原 2007-2009 老快照的反斜杠转义形态（raw 中占 458/2727）。
# 注：本轮未提升 PARSE_REV 取值，避免依赖修订号比对的离线工具被误触发全量重处理；
#     上述两处改动记录在本注释中备查。

# 隐藏节点（新浪模板用于放"引用来源""转载声明"等 UI 占位元素）
#
# 重要教训（2026-10-24 实测）：**不能**按 style="display:none" 过滤！
# 旧模板会把真实��文包在 <div style="DISPLAY: none" align="left">…</div> 中，
# 按此过滤会整段删掉正文（曾使 48874cec01008cyc 从 1062 字掉到 422 字）。
# 因此只处理有实证的 UI 元素，并附加"文本极短"保护：隐藏块内文本超过
# MAX_HIDDEN_TEXT 字时视为正文容器，予以保留。
HIDDEN_EL = re.compile(
    r'<(div|span|p|em|a)\b(?=[^>]*(?:class="[^"]*(?:invisible|vhidden)[^"]*"'
    r'|id="reference"'
    r'|id="Sina_AD2"))[^>]*>.*?</\1\s*>',
    re.DOTALL | re.I)

MAX_HIDDEN_TEXT = 120
_TAG = re.compile(r"<[^>]+>")


def _drop_hidden(m):
    """仅当隐藏元素内部是短文本（UI 占位）时删除，避免误删被隐藏的正文容器。"""
    inner = htmllib.unescape(_TAG.sub(" ", m.group(0)))
    inner = re.sub(r"\s+", "", inner).replace("\xa0", "")
    return " " if len(inner) <= MAX_HIDDEN_TEXT else m.group(0)

# 模板尾部碎片（即便隐藏元素未被整体匹配掉，也要从文末清掉）
TAIL_JUNK = re.compile(
    r'(?:[\s>]*(?:文章引用自|本文引用自|引用自|出处|来源)\s*[:：]?\s*'
    r'|<a\b[^>]*>\s*</a>|\?/P>|</?P>|&nbsp;)+$', re.I)


def clean_text(b):
    b = re.sub(r"<script.*?</script>", " ", b, flags=re.DOTALL | re.I)
    b = re.sub(r"<style.*?</style>", " ", b, flags=re.DOTALL | re.I)
    b = re.sub(r"<!--.*?-->", " ", b, flags=re.DOTALL)
    b = HIDDEN_EL.sub(_drop_hidden, b)          # P0：剔除隐藏 UI 占位元素
    b = re.sub(r"<br\s*/?>", "\n", b, flags=re.I)
    b = re.sub(r"</p>", "\n", b, flags=re.I)
    b = re.sub(r"<[^>]+>", " ", b)
    b = htmllib.unescape(b)                     # &#65292; -> ，  &#9733; -> ★
    b = b.replace("\u200b", "").replace("\xa0", " ")
    b = re.sub(r"[ \t\u3000]+", " ", b)
    b = re.sub(r"\n\s*\n+", "\n", b)
    b = TAIL_JUNK.sub("", b).strip()            # P0：清掉文末模板碎片
    return b


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
    # --- P0 修复（2026-10-24）：新浪模板常驻的隐���"引用来源"占位元素 ---
    # 实测：<div id="reference" class="invisible">文章引用自：<a href=""></a></div>
    # 位于正文容器最后一个子节点（片段 97%~99% 处），锚点为空、与转载无关，
    # 曾污染 558/632 篇产物。紧随其后是空广告位 #Sina_AD2。
    # 注意：只加入"有实证且不影响正文"的锚点；不要加 class 通配（可能出现在正文之前）。
    r'<div[^>]+id="reference', r'<div[^>]+id="Sina_AD2',
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


def unescape_archived(html):
    """还原"整页被当作 JS 字符串存档"的老快照（引号被反斜杠转义）。

    2026-10-25 实证：output/.../raw/ 中 458/2722（16.8%）为此形态，表现为
    `id=\\"articleContentArea\\"`、`\\n`、`\\t` 等。旧解析器在这些页面上
    **100% 抽不到正文**：124 条被判 no_body、334 条被误判为 photo_only（落盘成
    只有标题的空文）。同一页反转义后立刻可得 3038 字正文。
    2007-2009 年间的老博文普遍如此，而本轮缺口文章大多来自那个年代，
    故该修复对剩余清单的恢复率影响很大。

    用密度闸门（`\\"` 出现 > 20 次）避免误伤正常页面；正常页面为无操作。
    """
    if html.count('\\"') <= 20:
        return html
    return (html.replace('\\"', '"').replace("\\'", "'")
                .replace("\\n", "\n").replace("\\t", "\t")
                .replace("\\/", "/").replace("\\\\", "\\"))


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
    """返回 (status, data)。

    status ∈ ok / photo_only / permission / encrypted / no_body
            / throttled / proxy_error

    后两者是 2026-10-25 新增的**错误页识别**：反代或站点在自身被限流/故障时，
    可能仍以 HTTP 200 返回错误页（实测 allallow 返回 117 字节的
    "<h1>429 Too Many Requests</h1>"）。这类响应**不是文章页**，
    必须交给主流程按临时失败处理；否则会被判成 no_body 而写入永久跳过清单，
    把可恢复的文章静默判死（该缺陷已造成约 300 条误判，待复验）。
    """
    # 老快照的转义形态必须先还原，否则所有容器匹配式落空（见 unescape_archived）
    html = unescape_archived(html)
    # 错误页识别必须放在标题抽取之前：错误页若恰好带 <title>，会被误判成 photo_only 落盘。
    # 用"页面极小 + 典型错误特征"双重条件，避免误伤正文里恰好出现这些词的正常文章
    # （正常文章正文一般数十 KB，错误页仅百余字节）。
    if len(html) < 4000:
        if re.search(r"429\s+Too\s+Many\s+Requests|You have sent too many requests",
                     html, re.I):
            return "throttled", None
        if re.search(r"\b(?:502\s+Bad\s+Gateway|503\s+Service\s+Unavailable"
                     r"|504\s+Gateway\s+Time-?out)\b", html, re.I):
            return "proxy_error", None

    # ------------------------------------------------------------------
    # Wayback「无正文壳页」识别（2026-10-25 新增，对应跳过清单误判缺陷）
    # 现象：HTTP 200 返回约 1.9KB 的播放器壳页（只有 bundle-playback.js /
    #       wombat.js / ruffle.js，**没有 <body>**），正文一个字都没有。
    #       旧逻辑走到函数末尾判 no_body → 写入永久跳过清单。
    # 两类壳页必须分开处理（抽样复验 10-24 的 6/6 与 10-25 的 18/18 结果相反）：
    #   ① 壳页内带 JS 跳转到站点错误页（如 /main_v5/ria/error.html）：
    #      说明 CDX 索引里那条快照本身就是错误页跳转，**确无正文** → 仍判 no_body（永久）；
    #   ② 只有壳页、没有上述确证：很可能是反代/站点瞬时返回的壳，
    #      紧接着换节点重抓即可拿到正文 → 必须判 proxy_error（临时失败），
    #      **绝不能永久跳过**，否则把可恢复文章静默判死。
    # 注意：归档页脚普遍含 "playback timings" 字样，故必须同时要求"无 <body> 且极小"。
    if len(html) < 8000 and "<body" not in html.lower():
        if re.search(r"window\.location\.href\s*=\s*['\"][^'\"]*"
                     r"(?:error|sorry|notfound|404)", html, re.I):
            return "no_body", None
        if ("bundle-playback.js" in html or "wombat.js" in html
                or "playback timings" in html):
            return "proxy_error", None

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

    # 去掉正文里重复的标题行、日期行与模板残渣行，再判断真实长度
    if body:
        lines = [l.strip() for l in body.split("\n") if l.strip()]
        if title:
            t_norm = re.sub(r"\s+", "", title)
            kept = []
            for l in lines:
                ln = re.sub(r"\s+", "", l)
                if ln == t_norm:
                    continue                            # 整行等于标题
                if ln.startswith(t_norm):               # 旧模板：标题与日期同处一行
                    rest = ln[len(t_norm):]
                    if re.fullmatch(r"[（(]?20\d{2}-\d{2}-\d{2}[^）)]*[）)]?"
                                    r"(?:转载|转帖|转自|原创)?", rest):
                        continue
                kept.append(l)
            lines = kept
        lines = [l for l in lines
                 if not re.fullmatch(r"[（(]?\s*20\d{2}-\d{2}-\d{2}[^）)]*[）)]?\s*", l)]
        if len(body) < 120:                             # 极短正文只剩模板词时视为无正文
            lines = [l for l in lines
                     if not re.fullmatch(r"[（(]?\s*(?:转载|转帖|转自|引用|分享)\s*[）)]?", l)]
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


def unwrap_delivered(b):
    """反代返回的字节可能是 JSON 信封，返回候选字节形态 [(标签, bytes), ...]。

    用途：字节级校验。CDX 索引 digest 对应的是**未包装的原始页面字节**，
    若直接对信封字节求 SHA1 会必然不匹配（本次会话已踩坑），
    因此必须先把信封内 content 还原为字节，并区分编码往返形式。
    """
    cands = [("原始字节", b)]
    head = b[:1]
    if head in (b"{", b"["):
        try:
            j = json.loads(b.decode("utf-8", "ignore"))
        except Exception:
            j = None
        if isinstance(j, dict) and isinstance(j.get("content"), str):
            s = j["content"]
            cands.append(("JSON.content(utf-8 重编码)", s.encode("utf-8")))
            cands.append(("JSON.content(latin1 还原)", s.encode("latin-1", "ignore")))
    return cands


STRICT_REPOST = re.compile(
    r"(转载|转自|引自|引用自|摘自|文章来源|本文来源|内容来源|原文链接|原文出处)")


def detect_repost(content):
    """识别转载/引用痕迹，返回命中词（无则返回空串）。

    修正记录（2026-10-24）：新浪旧模板会在正文容器末尾放置隐藏占位元素
    <div id="reference" class="invisible">文章引用自：<a href=""></a></div>，
    这不是"引用块内容被剥离"，而是整站模板常驻的空壳，与是否转载无关。
    该元素已由解析器剔除（PARSE_REV=p0-20261024）；此处保留
    "标记之后仍需 >=30 字正文"的判据，作为二次兜底，避免残留文本导致误判。
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


# ---- 状态即时落盘（2026-10-24 补丁）----
# 原实现只在每轮结束后才写 skip/transient/warnings；轮中被中断会丢失本轮记录，
# 导致这些文章下轮被重复抓取。改为记录一旦产生立即原子落盘，格式与原来一致。
PATCH_REV = "persist-on-write-20261024"
_PERSISTED = {"skip": 0, "transient": 0, "warn": 0}
_CACHE = {"skip": None, "transient": None, "warn": None}


def save_json_atomic(path, obj):
    """原子写：先写同目录 .tmp 再 os.replace，避免中途中断留下半个文件。"""
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


NET_ERR_PAT = re.compile(
    r"getaddrinfo|name or service not known|name resolution|"
    r"10053|10054|10060|10061|10065|11001|"
    r"timed out|timeout|remote end closed|connection reset|connection refused|"
    r"unreachable|eof occurred|incompleteread|ssl|handshake|proxy", re.I)


def classify_error(msg):
    """把"临时失败"分为「网络类」与「限流类」。

    事故教训（2026-10-24 16:23–16:25）：本机 DNS 瞬时中断产生连续 8 次
    getaddrinfo failed，与站点限流共用同一个连续失败计数，直接触顶终止整轮，
    白停一轮。两类错误的正确处置不同：
      - 网络类：抖动通常几十秒到几分钟，应耐心重试，恢复即继续；
      - 限流类：说明我们在给站点压力，应尽快退让。
    因此分开计数（见 --max-net-fail），网络抖动不再误伤轮次。
    """
    return "net" if NET_ERR_PAT.search(msg or "") else "limit"


def persist_skips():
    """永久跳过清单即时落盘（累积，不覆盖历史）。"""
    if _CACHE["skip"] is None:
        _CACHE["skip"] = load_json(SKIP_FILE, {})
    d = _CACHE["skip"]
    if not isinstance(d, dict):
        d = {}
    # 2026-10-25 起补记 node 与 parse_rev：便于日后定向复验"这条跳过是谁判的、按哪版解析器判的"。
    # 此前只记 url/reason/at，导致 allallow 造成的误判无法逐条反查（只能用比例估算）。
    # 旧记录缺这两个字段属正常，不要回填猜测值。
    for s in SKIPPED[_PERSISTED["skip"]:]:
        d[s["article_id"]] = {"url": s["url"], "reason": s["reason"],
                              "node": s.get("node", ""),
                              "parse_rev": PARSE_REV,
                              "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    _PERSISTED["skip"] = len(SKIPPED)
    save_json_atomic(SKIP_FILE, d)


def persist_transient():
    """临时失败清单即时落盘（累积去重，便于评估通道健康度）。"""
    if _CACHE["transient"] is None:
        _CACHE["transient"] = load_json(FAIL_FILE, [])
    d = _CACHE["transient"]
    if not isinstance(d, list):
        d = []
    seen = set(r.get("article_id") for r in d if isinstance(r, dict))
    for r in TRANSIENT[_PERSISTED["transient"]:]:
        if r["article_id"] not in seen:
            d.append(r)
            seen.add(r["article_id"])
    _PERSISTED["transient"] = len(TRANSIENT)
    save_json_atomic(FAIL_FILE, d)


def node_bump(name, key, n=1):
    """节点观测自增。只记录，不参与任何抓取决策。"""
    d = NODE_STATS.get(name)
    if not isinstance(d, dict):
        d = {}
        NODE_STATS[name] = d
    for k in NODE_KEYS:
        d.setdefault(k, 0)
    d[key] = d.get(key, 0) + n
    d["last"] = time.strftime("%Y-%m-%d %H:%M:%S")


def load_nodes():
    """载入历史节点观测（跨轮累积，不重置）。"""
    d = load_json(NODE_FILE, {})
    if isinstance(d, dict):
        for k, v in d.items():
            if isinstance(v, dict):
                NODE_STATS[k] = v


def node_round_delta(base):
    """相对本轮开始时的增量，用于在日志里如实报"本轮"而不是累计。"""
    out = []
    for name in sorted(NODE_STATS):
        cur, was = NODE_STATS[name], base.get(name, {})
        d = {k: cur.get(k, 0) - was.get(k, 0) for k in NODE_KEYS}
        if d["try"]:
            out.append("%s 试%d 成%d 跳%d 失%d(429:%d 网络:%d)"
                       % (name, d["try"], d["ok"] + d["photo"], d["skip"], d["fail"],
                          d["err429"], d["errnet"]))
    return out


def persist_nodes():
    """节点观测落盘（累积，不做去重）。"""
    try:
        save_json_atomic(NODE_FILE, NODE_STATS)
    except Exception as e:
        log("  节点观测落盘失败：%s" % str(e)[:60])


def persist_warnings():
    """异常样本清单即时落盘（累积去重）。"""
    if _CACHE["warn"] is None:
        _CACHE["warn"] = load_json(WARN_FILE, [])
    d = _CACHE["warn"]
    if not isinstance(d, list):
        d = []
    seen = set(r.get("article_id") for r in d if isinstance(r, dict))
    for r in WARNINGS[_PERSISTED["warn"]:]:
        if r["article_id"] not in seen:
            d.append(r)
            seen.add(r["article_id"])
    _PERSISTED["warn"] = len(WARNINGS)
    save_json_atomic(WARN_FILE, d)




def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gap", type=float, default=15.0, help="两次请求间隔秒数（默认 15，勿低于 10）")
    ap.add_argument("--seconds", type=float, default=780, help="本轮最长运行秒数")
    ap.add_argument("--limit", type=int, default=0, help="本轮最多抓取篇数（0=不限）")
    ap.add_argument("--max-fail", type=int, default=8, help="连续限流类失败达到该值即提前停止（默认 8）")
    ap.add_argument("--max-net-fail", type=int, default=20,
                    help="连续网络类失败达到该值即提前停止本轮（默认 20；"
                         "网络抖动与站点限流分开计数，避免 DNS 抖动误停整轮）")
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
    log("解析器 %s | 清单 %d 篇 | 已抓 %d | 永久跳过 %d | 待抓 %d"
        % (PARSE_REV, total_list, len(done), len(skip), len(todo)))
    load_nodes()
    node_base = {k: dict(v) for k, v in NODE_STATS.items()}
    if not todo:
        log("无待抓条目，全部完成或已跳过")
        return

    t0 = time.time()
    i = 0
    consec = 0
    consec_net = 0
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
        node_bump(name, "try")
        if n:
            time.sleep(args.gap + random.uniform(0, args.gap * 0.2))
        # 统一失败路径（2026-10-25）：网络异常与"反代错误页"都归到这里，共用一套计数与退让逻辑。
        # 关键约束：status 为 throttled / proxy_error 时**绝不写永久跳过**，只记临时失败等重试。
        err = None                      # (msg, kind)
        try:
            html, delivered, wrapped = fetch_one(name, tmpl, v["timestamp"], v["url"])
            status, p = parse_article(html) if html else ("no_body", None)
        except Exception as e:
            status, p = "transient", None
            _m = str(e)[:70]
            err = (_m, classify_error(_m))
        if err is None and status == "throttled":
            status, p = "transient", None
            err = ("反代错误页：429 Too Many Requests（HTTP 200 伪装，非文章页）", "limit")
        if err is None and status == "proxy_error":
            status, p = "transient", None
            err = ("反代错误页：5xx 网关故障（非文章页）", "net")
        if err is not None:
            msg, kind = err
            TRANSIENT.append({"article_id": aid, "node": name, "error": msg, "kind": kind})
            persist_transient()
            STATS["transient"] += 1
            node_bump(name, "fail")
            if "429" in msg:
                node_bump(name, "err429")
            elif kind == "net":
                node_bump(name, "errnet")
            else:
                node_bump(name, "err_other")
            persist_nodes()
            log("  临时失败(%s) %s: %s" % (name, aid, msg))
            if kind == "net":
                consec_net += 1
                consec = 0
                log("     分类：网络类（连续 %d/%d）—— 网络抖动，恢复后自动继续"
                    % (consec_net, args.max_net_fail))
                if consec_net >= args.max_net_fail:
                    log("连续网络类失败 %d 次，提前停止本轮（非站点限流；"
                        "网络恢复后重跑即可续抓）" % consec_net)
                    break
            else:
                consec += 1
                consec_net = 0
                log("     分类：限流类（连续 %d/%d）—— 疑似站点限流，应从速退让"
                    % (consec, args.max_fail))
                if consec >= args.max_fail:
                    log("连续限流类失败 %d 次，提前停止（疑似限流，稍后重跑即可续抓）" % consec)
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
                f.write("parse_rev: %s\n" % PARSE_REV)
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
                    f.write("content_quality: empty\n")
                else:
                    f.write("content_quality: %s\n"
                            % ("thin" if len(p["content"]) < 200 else "full"))
                f.write("---\n\n%s\n" % p["content"])
            if status == "photo_only":
                STATS["photo"] += 1
                node_bump(name, "photo")
                log("  纯图片/无正文 %s《%s》" % (aid, p["title"][:24]))
            else:
                STATS["ok"] += 1
                node_bump(name, "ok")
            consec = 0
            consec_net = 0
            if len(p["content"]) > 30000:
                WARNINGS.append({"article_id": aid, "len": len(p["content"]),
                                 "note": "正文超长，需人工确认是否混入多篇"})
                persist_warnings()
            if STATS["ok"] % 10 == 0:
                el = time.time() - t0
                log("  已抓 %d 篇（经 %.0fs，%.1f 篇/分钟）| 累计 %d/%d"
                    % (STATS["ok"], el, STATS["ok"] / max(el / 60, 0.01), len(done) + STATS["ok"], total_list))
                persist_nodes()
        else:
            SKIPPED.append({"article_id": aid, "url": v["url"], "reason": status,
                            "node": name})
            persist_skips()
            STATS["skip"] += 1
            node_bump(name, "skip")
            persist_nodes()
            consec = 0
            consec_net = 0
            # 日志行格式保持不变（统计脚本按 "永久跳过(原因)" 解析）；来源节点只写入 JSON 清单
            log("  永久跳过(%s) %s" % (status, aid))

    el = time.time() - t0
    log("-" * 64)
    log("本轮：成功 %d | 纯图片 %d | 永久跳过 %d | 临时失败 %d | 耗时 %.0fs（%.1f 篇/分钟）"
        % (STATS["ok"], STATS["photo"], STATS["skip"], STATS["transient"], el,
           (STATS["ok"] + STATS["photo"]) / max(el / 60, 0.01)))
    rows = node_round_delta(node_base)
    if rows:
        log("  节点观测（本轮，仅统计不影响抓取）：" + " | ".join(rows))

    # 合并落盘（每篇已即时落盘，此处仅作最终一致性写入，避免覆盖）
    persist_skips()
    persist_transient()
    persist_warnings()
    persist_nodes()
    old_skip = load_json(SKIP_FILE, {})

    allmd = glob.glob(os.path.join(POSTS_DIR, "*.md"))
    log("累计已抓 %d / %d 篇（%.1f%%）| 永久跳过累计 %d 篇"
        % (len(allmd), total_list, len(allmd) * 100.0 / total_list, len(old_skip)))


if __name__ == "__main__":
    main()