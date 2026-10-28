# -*- coding: utf-8 -*-
"""正文质量告警（口径 v2，2026-10-24 修订）。

修订原因：旧口径"纯长度 <60 字 / 60-200 字"会把**真实短文**误报为缺陷。
实测（12 份有 raw 存档的样本）容器纯文本 ≈ 落盘正文，差异仅 &nbsp; 空白，
即博主本就大量发布一句话式博文（股票名单、单句点评）。

新口径：**先剔除模板残留，再按结构特征分类**，只有结构性残渣才告警。

分类与动作：
  full                 正文充足                    -> 无需动作
  thin_real_short      剔除残留后仍 <200 字的短文   -> 无需动作（抽样复核清单）
  title_date_residue   只剩标题/日期/转载等模板残渣 -> **告警**：建议重抓重解析
  empty_body           正文为空（纯图片博文）        -> 无需动作（已正确标记）

用法：
  python tools/quality_alarm.py                # 扫描 posts/（抓取产物）
  python tools/quality_alarm.py --dir clean    # 扫描 clean/（清洗副本）
"""
import io, os, re, sys, glob, time
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "output", "1216826604_wayback")
INTEG = os.path.join(OUT, "integrity")

TAIL = re.compile(
    r'(?:\s*(?:文章引用自|本文引用自|引用自|出处|文章来源|本文来源)\s*[:：]?'
    r'|(?:\?/P>|</?P>|&nbsp;|[\s|┆·]+))+$', re.I)
DATE_ONLY = re.compile(r"^[（(]?\s*20\d{2}-\d{2}-\d{2}[^）)]*[）)]?$")
TMPL_WORD = re.compile(r"^(?:转载|转帖|转自|引用|分享|收藏|打印|举报|评论|查看全文)$")


def strip_residue(body):
    prev = None
    while prev != body:
        prev = body
        body = TAIL.sub("", body).strip()
    return body


def classify(body, fm):
    """返回 (分类, 建议动作)"""
    if not body or fm.get("content_type") == "photo_only" \
            or fm.get("content_quality") == "empty":
        return "empty_body", "none"
    lines = [l.strip() for l in body.split("\n") if l.strip()]
    # 是否只剩标题/日期/模板词
    residue_lines = [l for l in lines
                     if DATE_ONLY.match(l) or TMPL_WORD.match(l)]
    title = (fm.get("title") or "").strip()
    if title:
        tn = re.sub(r"\s+", "", title)
        residue_lines += [l for l in lines if re.sub(r"\s+", "", l) == tn]
    if len(residue_lines) == len(lines) and len(body) < 120:
        return "title_date_residue", "refetch"
    # 增强判定：短正文中"标题 + 日期 + 转载"同行混排（旧模板典型形态）
    if len(body) < 120 and re.search(r"20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2}", body):
        t = body.replace(title, "") if title else body
        t = re.sub(r"20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2}(?::\d{2})?", "", t)
        t = re.sub(r"(转载|转帖|转自|引用|分享|本文来源)", "", t)
        t = re.sub(r"[\s（）()【】\[\]\-—|┆·。，,.:：!！?？…]+", "", t)
        if len(t) < 8:
            return "title_date_residue", "refetch"
    if len(body) < 200:
        return "thin_real_short", "sample_review"
    return "full", "none"


def main():
    src = "posts"
    if "--dir" in sys.argv:
        src = sys.argv[sys.argv.index("--dir") + 1]
    base = os.path.join(OUT, src)
    tag = "clean" if src.startswith("clean") else "posts"
    report = os.path.join(INTEG, "quality_alarm_%s_%s.tsv"
                          % (tag, time.strftime("%Y%m%d")))

    rows, cnt = [], Counter()
    actions = Counter()
    for fp in sorted(glob.glob(os.path.join(base, "*.md"))):
        aid = os.path.basename(fp)[:-3]
        t = io.open(fp, encoding="utf-8", errors="ignore").read()
        parts = t.split("---", 2)
        if len(parts) < 3:
            continue
        fm = dict((m.group(1), m.group(2).strip())
                  for m in re.finditer(r"^([a-z0-9_]+):\s*(.*?)\s*$", parts[1], re.M))
        raw_body = parts[2].strip()
        body = strip_residue(raw_body)
        cls, act = classify(body, fm)
        cnt[cls] += 1
        actions[act] += 1
        note = "残留" if "引用自" in raw_body else ""
        if act != "none" or note:      # 明细只记"需要关注的"：待重抓/待抽样/有残留
            rows.append((aid, len(raw_body), len(body), cls, act, note))

    with io.open(report, "w", encoding="utf-8", newline="") as f:
        f.write("# 口径 v2：先剔模板残留，再按结构判定（%s）\n"
                % time.strftime("%Y-%m-%d %H:%M:%S"))
        f.write("article_id\tbody_len\tlen_after_strip\tclass\taction\tnote\n")
        for r in rows:
            f.write("\t".join(str(x) for x in r) + "\n")

    n = sum(cnt.values())
    print("### 正文质量告警（口径 v2，来源 %s/，共 %d 篇）" % (src, n))
    order = [("full", "正文充足"), ("thin_real_short", "真实短文（非缺陷）"),
             ("title_date_residue", "只剩模板残渣（告警）"), ("empty_body", "空正文/纯图片")]
    for k, label in order:
        print("  %-20s %5d 篇 (%.1f%%)  %s" % (k, cnt[k], 100.0 * cnt[k] / max(n, 1), label))
    print("  残留模板标记          %5d 篇 (须为 0)"
          % sum(1 for r in rows if r[5] == "残留"))
    print("\n### 建议动作")
    for a, c in actions.most_common():
        desc = {"none": "无需动作", "sample_review": "进入抽样复核清单",
                "refetch": "**建议重抓重解析**"}.get(a, a)
        print("  %-14s %5d 篇  %s" % (a, c, desc))
    print("\n报告: %s" % report)


if __name__ == "__main__":
    main()