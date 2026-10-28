# -*- coding: utf-8 -*-
"""把某博主的全部文章按发布时间顺序导出为一个 .docx。

为什么不用 python-docx 逐段构建：
    徐小明 12782 篇 / 24MB 正文，逐段 add_paragraph 会产生数十万个 lxml 节点，
    内存可达 GB 级且耗时数分钟。本脚本改为**流式生成 OOXML**：
    直接按 XML 片段写入 word/document.xml，再与 styles/rels 一起打包，
    内存恒定（只保留单篇正文），12782 篇约 30 秒。

文档结构：
    封面  →  各年分节（Heading1，每年另起一页）  →  每篇（Heading2 = 时间 + 标题）  →  正文
    Word 导航窗格可直接按年份/文章跳转。

用法：
    python tools/blog_to_docx.py --uid 1300871220 --name 徐小明
    python tools/blog_to_docx.py --all                # 缠中说禅 + 股市风云 + 徐小明
    python tools/blog_to_docx.py --uid 1300871220 --dry-run
"""
from __future__ import print_function

import argparse
import glob
import io
import json
import os
import re
import sys
import tempfile
import time
import zipfile
from collections import OrderedDict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT = os.path.join(ROOT, "output")
DOCX_DIR = os.path.join(ROOT, "docx")

# 未指定 --name 时按 uid 兜底取名
DEFAULT_BLOGGERS = [
    ("1215172700", "缠中说禅"),
    ("1285707277", "股市风云"),
    ("1300871220", "徐小明"),
]

RE_FM = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.S)
RE_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
CHUNK = 4000          # 单段落最大字符数，超长正文按句读切段
SPLITERS = "。！？；…\n"


# ── 数据加载 ────────────────────────────────────────────────────
def load_index(uid):
    """返回 {article_id: {'title','pub_time',...}}，按 pub_time 缺失与否也记录了。"""
    p = os.path.join(OUTPUT, uid, "posts", "index.json")
    if not os.path.exists(p):
        return {}
    data = json.load(io.open(p, encoding="utf-8"))
    out = {}
    for it in (data.get("posts") or []):
        aid = it.get("article_id")
        if aid:
            out[aid] = it
    return out


def split_frontmatter(text):
    m = RE_FM.match(text)
    if not m:
        return "", text
    return m.group(1), text[m.end():]


def load_posts(uid):
    """返回 [(pub_time, article_id, title, body)]，按时间升序；缺时间者排在最后。"""
    idx = load_index(uid)
    files = sorted(glob.glob(os.path.join(OUTPUT, uid, "posts", "*.md")))
    recs = []
    for f in files:
        aid = os.path.splitext(os.path.basename(f))[0]
        try:
            with io.open(f, encoding="utf-8", errors="replace") as fh:
                raw = fh.read()
        except Exception as e:
            print("  [warn] 读取失败 %s: %s" % (aid, e))
            continue
        _, body = split_frontmatter(raw)
        body = body.strip()
        it = idx.get(aid) or {}
        title = (it.get("title") or "").strip()
        ptime = (it.get("pub_time") or "").strip()
        if not title:
            # 缺标题时用正文首句代替，便于在导航窗格里辨认
            head = re.split(r"[。！？\n]", body, 1)[0].strip()
            title = head[:28] if head else "（无标题）"
        recs.append((ptime, aid, title, body))
    recs.sort(key=lambda r: (r[0] or "9999-99-99", r[1]))
    return recs


# ── XML 片段 ────────────────────────────────────────────────────
def esc(s):
    s = RE_CTRL.sub("", s)
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


def para(text, style=None, align=None, bold=False, size=None, color=None,
         space_before=None, space_after=None, page_break=False, indent_first=False):
    ppr = []
    if style:
        ppr.append('<w:pStyle w:val="%s"/>' % style)
    if page_break:
        ppr.append("<w:pageBreakBefore/>")
    sp = []
    if space_before is not None:
        sp.append('w:before="%d"' % space_before)
    if space_after is not None:
        sp.append('w:after="%d"' % space_after)
    if sp:
        ppr.append("<w:spacing %s/>" % " ".join(sp))
    if indent_first:
        ppr.append('<w:ind w:firstLineChars="200" w:firstLine="440"/>')
    if align:
        ppr.append('<w:jc w:val="%s"/>' % align)

    rpr = []
    if bold:
        rpr.append("<w:b/>")
    if color:
        rpr.append('<w:color w:val="%s"/>' % color)
    if size:
        rpr.append('<w:sz w:val="%d"/><w:szCs w:val="%d"/>' % (size, size))

    run = ""
    if text:
        run = "<w:r>%s<w:t xml:space=\"preserve\">%s</w:t></w:r>" % (
            ("<w:rPr>%s</w:rPr>" % "".join(rpr)) if rpr else "", esc(text))
    return "<w:p>%s%s</w:p>" % (
        ("<w:pPr>%s</w:pPr>" % "".join(ppr)) if ppr else "", run)


def chunks(body):
    """把超长正文按句读切成 <=CHUNK 的段，避免单段落过长导致 Word 异常。"""
    body = body.strip()
    if not body:
        return ["（原文正文为空）"]
    if len(body) <= CHUNK:
        return [body]
    out, buf = [], ""
    for ch in body:
        buf += ch
        if len(buf) >= CHUNK and ch in SPLITERS:
            out.append(buf)
            buf = ""
    if buf:
        while len(buf) > CHUNK:          # 无标点长串，硬切
            out.append(buf[:CHUNK])
            buf = buf[CHUNK:]
        if buf:
            out.append(buf)
    return out


# ── docx 骨架 ───────────────────────────────────────────────────
NS_W = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"')

STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles %s>
<w:docDefaults>
<w:rPrDefault><w:rPr><w:rFonts w:ascii="Times New Roman" w:eastAsia="\u5b8b\u4f53" w:hAnsi="Times New Roman" w:cs="Times New Roman"/><w:sz w:val="22"/><w:szCs w:val="22"/><w:lang w:val="en-US" w:eastAsia="zh-CN"/></w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:line="336" w:lineRule="auto"/></w:pPr></w:pPrDefault>
</w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:spacing w:before="2400" w:after="360"/><w:jc w:val="center"/></w:pPr><w:rPr><w:rFonts w:eastAsia="\u9ed1\u4f53"/><w:b/><w:sz w:val="56"/><w:szCs w:val="56"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Subtitle"><w:name w:val="Subtitle"/><w:basedOn w:val="Normal"/><w:qFormat/><w:pPr><w:spacing w:after="120"/><w:jc w:val="center"/></w:pPr><w:rPr><w:rFonts w:eastAsia="\u9ed1\u4f53"/><w:sz w:val="24"/><w:szCs w:val="24"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="360" w:after="240"/><w:jc w:val="center"/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:rFonts w:eastAsia="\u9ed1\u4f53"/><w:b/><w:sz w:val="40"/><w:szCs w:val="40"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="300" w:after="100"/><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:rFonts w:eastAsia="\u9ed1\u4f53"/><w:b/><w:sz w:val="26"/><w:szCs w:val="26"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="BodyText"><w:name w:val="Body Text"/><w:basedOn w:val="Normal"/><w:qFormat/><w:pPr><w:spacing w:after="140" w:line="360" w:lineRule="auto"/><w:ind w:firstLineChars="200" w:firstLine="440"/></w:pPr></w:style>
</w:styles>""" % NS_W

CORE = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<dc:title>%s</dc:title><dc:creator>blog-downloader</dc:creator><cp:lastModifiedBy>blog-downloader</cp:lastModifiedBy>
<dcterms:created xsi:type="dcterms:W3CDTF">%s</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">%s</dcterms:modified>
</cp:coreProperties>"""

SECT = ('<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1418" w:right="1418" w:bottom="1418" w:left="1418" '
        'w:header="851" w:footer="992" w:gutter="0"/></w:sectPr>')


# ── 主流程 ──────────────────────────────────────────────────────
def build(uid, name, out_path, dry_run=False):
    t0 = time.time()
    recs = load_posts(uid)
    if not recs:
        print("  [skip] %s 没有文章" % uid)
        return None
    dated = [r for r in recs if r[0]]
    first, last = (dated[0][0] if dated else "?"), (dated[-1][0] if dated else "?")
    print("[%s] %s：%d 篇（有日期 %d，缺日期 %d），%s ~ %s"
          % (uid, name, len(recs), len(dated), len(recs) - len(dated), first[:10], last[:10]))

    by_year = OrderedDict()
    for r in recs:
        by_year.setdefault(r[0][:4] if r[0] else "时间缺失", []).append(r)

    if dry_run:
        print("  [dry-run] 分年: %s" % {k: len(v) for k, v in by_year.items()})
        print("  [dry-run] 预计输出 %s" % out_path)
        return None

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime())

    tmp_xml = out_path + ".document.xml.tmp"
    n_para = 0
    with io.open(tmp_xml, "w", encoding="utf-8", newline="") as f:
        f.write('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:document %s><w:body>' % NS_W)

        # 封面
        f.write(para("%s 博客文集" % name, style="Title"))
        f.write(para("共 %d 篇 ｜ %s ~ %s" % (len(recs), first[:10], last[:10]), style="Subtitle"))
        f.write(para("新浪博客 uid %s" % uid, style="Subtitle"))
        f.write(para("导出时间 %s" % stamp, style="Subtitle"))
        f.write(para(""))
        f.write(para("说明：全文按发布时间升序排列，每一年为一节；缺标题者以正文首句代替，"
                     "缺发布时间者统一归入文末「时间缺失」一节。", align="center", color="595959", size=20))
        f.write(para(""))

        # 目录（年份 + 篇数，静态列表，无需 Word 更新域）
        f.write(para("目　录", style="Heading1", page_break=True))
        for yr, rs in by_year.items():
            f.write(para("%s　%s 篇" % (yr, len(rs)), align="center", space_after=60))
        n_para += 5 + len(by_year)

        # 正文
        for yr, rs in by_year.items():
            label = "%s 年" % yr if yr.isdigit() else yr
            f.write(para(label, style="Heading1", page_break=True))
            n_para += 1
            for ptime, aid, title, body in rs:
                head = ("%s　%s" % (ptime[:16], title)) if ptime else title
                f.write(para(head, style="Heading2"))
                for c in chunks(body):
                    f.write(para(c, style="BodyText"))
                n_para += 2

        f.write(SECT)
        f.write("</w:body></w:document>")

    # 用 python-docx 的完整包骨架（含 theme/settings/fontTable 等部件）承载正文。
    # 直接输出最小包也能开，但部分 Office 版本会报“内容不可读”，套现成骨架最稳妥。
    try:
        import docx as _docx
    except ImportError:
        print("  [error] 需要 python-docx：pip install python-docx")
        return None
    base_fd, base_path = tempfile.mkstemp(suffix=".docx")
    os.close(base_fd)
    _docx.Document().save(base_path)

    with zipfile.ZipFile(base_path) as src, \
            zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as dst:
        for item in src.infolist():
            nm = item.filename
            if nm == "word/document.xml":
                dst.write(tmp_xml, nm)
            elif nm == "word/styles.xml":
                dst.writestr(nm, STYLES)
            elif nm == "docProps/core.xml":
                dst.writestr(nm, CORE % (esc("%s 博客文集" % name), now, now))
            else:
                dst.writestr(item, src.read(nm))

    os.remove(tmp_xml)
    os.remove(base_path)

    size = os.path.getsize(out_path)
    print("  → %s  (%.1f MB, %d 段落, 耗时 %.0fs)"
          % (os.path.relpath(out_path, ROOT), size / 1048576.0, n_para, time.time() - t0))
    return out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uid")
    ap.add_argument("--name")
    ap.add_argument("--all", action="store_true", help="导出缠中说禅/股市风云/徐小明三个博主")
    ap.add_argument("--out-dir", default=DOCX_DIR)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.all:
        targets = DEFAULT_BLOGGERS
    elif args.uid:
        targets = [(args.uid, args.name or args.uid)]
    else:
        ap.error("请指定 --uid 或 --all")

    results = []
    for uid, name in targets:
        out = os.path.join(args.out_dir, "%s_博客文集.docx" % name)
        p = build(uid, name, out, args.dry_run)
        if p:
            results.append(p)

    if results:
        print("\n完成 %d 个文档：" % len(results))
        for p in results:
            print("  %s  %.1f MB" % (os.path.relpath(p, ROOT), os.path.getsize(p) / 1048576.0))


if __name__ == "__main__":
    main()
