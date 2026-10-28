# -*- coding: utf-8 -*-
"""生成存量产物的"清洗副本"（原件一字不动）。

策略（按保真度优先）：
  1. 有 raw 原始 HTML  -> 用 P0 解析器重新解析（method=reparse）
     并做严格校验：与旧正文的差异必须只落在模板碎片白名单内，
     否则回退为文本级清理并标记 needs_review。
  2. 无 raw            -> 文本级清理文末模板碎片（method=tail_strip）

输出：
  output/1216826604_wayback/clean/<id>.md        清洗副本
  output/1216826604_wayback/integrity/clean_manifest.tsv  处理台账
"""
import gzip, io, os, re, sys, glob, json, difflib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import wayback_resume as W  # noqa: E402

OUT = os.path.join(ROOT, "output", "1216826604_wayback")
CLEAN = os.path.join(OUT, "clean")
MANIFEST = os.path.join(OUT, "integrity", "clean_manifest.tsv")
P0_REV = W.PARSE_REV
CLEAN_REV = "clean-20261024"

# 文末模板碎片（文本级兜底清理）
TAIL = re.compile(
    r'(?:\s*(?:文章引用自|本文引用自|引用自|出处|文章来源|本文来源)\s*[:：]?'
    r'|(?:\?/P>|</?P>|&nbsp;|[\s|┆·]+))+$', re.I)

# 允许的差异（模板碎片白名单）
ALLOW = re.compile(
    r"^(?:[\s\S]*?(?:文章引用自|本文引用自|引用自|出处|文章来源|本文来源)\s*[:：]?[\s\S]*"
    r"|[\s?/P>&nbsp;|┆·\-—]*)$", re.I)


def diff_is_junk_only(old, new):
    sm = difflib.SequenceMatcher(None, old, new, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("delete", "replace"):
            lost = old[i1:i2]
            if len(lost.strip()) >= 12 and not ALLOW.match(lost.strip()):
                return False, lost[:100]
    return True, ""


def tail_strip(body):
    prev = None
    while prev != body:
        prev = body
        body = TAIL.sub("", body).strip()
    return body


def split_md(text):
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None, text
    return parts[1], parts[2]


def main():
    if not os.path.isdir(CLEAN):
        os.makedirs(CLEAN)
    rows = []
    n_reparse = n_strip = n_review = n_skip = n_unchanged = 0
    total_delta = 0

    for fp in sorted(glob.glob(os.path.join(W.POSTS_DIR, "*.md"))):
        aid = os.path.basename(fp)[:-3]
        text = io.open(fp, encoding="utf-8", errors="ignore", newline="").read()
        fm, body = split_md(text)
        if fm is None:
            continue
        body_clean = body.strip().replace("\r\n", "\n")

        if ("parse_rev: %s" % P0_REV) in fm:
            n_skip += 1                      # P0 之后产出，本就干净，无需清洗
            continue

        raw_file = None
        m = re.search(r"^raw_file:\s*(.+?)\s*$", fm, re.M)
        if m:
            raw_file = os.path.join(OUT, m.group(1).replace("/", os.sep))

        method, note, new_body = "tail_strip", "", tail_strip(body_clean)

        if raw_file and os.path.exists(raw_file):
            html = gzip.open(raw_file, "rb").read().decode("utf-8", "ignore")
            if "raw_wrapped: 1" in fm:
                try:
                    html = json.loads(html).get("content", "")
                except Exception:
                    pass
            try:
                _, pd = W.parse_article(html)
                cand = (pd or {}).get("content", "").strip()
                good, lost = diff_is_junk_only(body_clean, cand)
                if good:
                    method, new_body = "reparse", cand
                else:
                    n_review += 1
                    note = "reparse_diff_rejected:%s" % re.sub(r"\s+", " ", lost)[:60]
            except Exception as e:
                note = "reparse_error:%s" % str(e)[:60]

        if method == "reparse":
            n_reparse += 1
        elif not note:
            n_strip += 1

        if new_body == body_clean:
            n_unchanged += 1

        # 写入副本：保留原 frontmatter，追加清洗标记
        new_fm = fm.rstrip("\n")
        new_fm += "\nclean_rev: %s\nclean_method: %s\nclean_from: posts/%s.md\n" % (
            CLEAN_REV, method, aid)
        if note:
            new_fm += "clean_note: %s\n" % note
        with io.open(os.path.join(CLEAN, aid + ".md"), "w",
                     encoding="utf-8", newline="") as f:
            f.write("---%s---\n\n%s\n" % (new_fm, new_body))

        delta = len(body_clean) - len(new_body)
        total_delta += delta
        rows.append((aid, method, len(body_clean), len(new_body), delta, note))

    with io.open(MANIFEST, "w", encoding="utf-8", newline="") as f:
        f.write("article_id\tmethod\told_len\tnew_len\tdelta\tnote\n")
        for r in rows:
            f.write("\t".join(str(x) for x in r) + "\n")

    print("清洗副本目录: clean/")
    print("  已处理            : %d 篇" % len(rows))
    print("    ├ reparse(重解析) : %d" % n_reparse)
    print("    └ tail_strip(文本): %d" % n_strip)
    print("  回退并标记复核    : %d" % n_review)
    print("  内容无变化        : %d" % n_unchanged)
    print("  跳过(P0 后已干净) : %d" % n_skip)
    print("  累计删除字符      : %d" % total_delta)
    print("  台账: %s" % MANIFEST)


if __name__ == "__main__":
    main()