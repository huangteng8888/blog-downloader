# -*- coding: utf-8 -*-
"""
交叉比对：心明禅师转载的 310 篇  vs  原博客已有的 2404 篇

方法：
    标题不可靠（转载标题被改过），因此用【正文指纹】做判定——
    对正文抽取 20 字符的 n-gram（去掉标点空白），建立原博客的倒排索引，
    再用转载文章的 n-gram 去查，命中率高的即为"同一篇文章"。

判定：
    命中率 >= 0.30  → 与原博客已有内容重复（不需要补充）
    命中率 <  0.30  → 原博客没有的内容（潜在可补充）
        再与"原博客缺失清单(2620条)"做标题匹配：
            匹配上   → A 类：补上了原博客缺失的文章
            未匹配上 → B 类：原博客清单里也没有的独立内容

产物：
    temp/cross_check_result.json    完整比对结果
    temp/cross_check_report.txt     可读报告
"""
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORIG_DIR = os.path.join(ROOT, "output", "1216826604", "posts")
SUPP_DIR = os.path.join(ROOT, "output", "1216826604_supplement", "posts")
TODO_FILE = os.path.join(ROOT, "temp", "wu2198_todo.json")
OUT_JSON = os.path.join(ROOT, "temp", "cross_check_result.json")
OUT_TXT = os.path.join(ROOT, "temp", "cross_check_report.txt")

N = 20
STEP = 4
DUP_THRESHOLD = 0.30

lines = []


def log(s=""):
    print(s)
    lines.append(s)


def body_of(path):
    t = io.open(path, encoding="utf-8", errors="ignore").read()
    b = re.split(r"^---\n.*?\n---\n", t, maxsplit=1, flags=re.DOTALL)
    return (b[1] if len(b) > 1 else b[0]).strip()


def grams(text):
    t = re.sub(r"[^\w\u4e00-\u9fff]", "", text or "")
    if len(t) < N:
        return set()
    return set(t[i:i + N] for i in range(0, len(t) - N, STEP))


def norm_title(s):
    s = re.sub(r"^转[-\s]*", "", s or "")
    s = re.sub(r"^(wu2198|wu先生|wu)[-\s]*", "", s, flags=re.I)
    return re.sub(r"[\s，。！？、：；《》()（）\-—…,.!?\"'“”]", "", s)


def main():
    log("=" * 74)
    log("转载文章 vs 原博客 正文指纹比对")
    log("=" * 74)

    # 1) 原博客倒排索引
    log("构建原博客正文索引 ...")
    inverted = {}
    orig_ids = []
    for f in sorted(os.listdir(ORIG_DIR)):
        if not f.endswith(".md"):
            continue
        aid = f[:-3]
        g = grams(body_of(os.path.join(ORIG_DIR, f)))
        orig_ids.append(aid)
        for x in g:
            inverted.setdefault(x, []).append(aid)
    log("  原博客: %d 篇，索引 n-gram %d 个" % (len(orig_ids), len(inverted)))

    # 2) 逐篇比对
    log("-" * 74)
    log("比对 %s ..." % SUPP_DIR)
    supp_idx = {}
    if os.path.exists(os.path.join(SUPP_DIR, "..", "index.json")):
        d = json.load(io.open(os.path.join(SUPP_DIR, "..", "index.json"), encoding="utf-8"))
        supp_idx = {p["article_id"]: p for p in d["posts"]}

    results = []
    for f in sorted(os.listdir(SUPP_DIR)):
        if not f.endswith(".md"):
            continue
        sid = f[:-3]
        body = body_of(os.path.join(SUPP_DIR, f))
        g = grams(body)
        meta = supp_idx.get(sid, {})
        row = {
            "supp_id": sid,
            "title": meta.get("title", ""),
            "pub_time": meta.get("pub_time", ""),
            "orig_pub_time": meta.get("orig_pub_time", ""),
            "category": meta.get("category", "").replace("\n", " ").replace("\t", " ").strip(),
            "chars": len(body),
            "gram_cnt": len(g),
            "best_orig": "",
            "best_hits": 0,
            "rate": 0.0,
        }
        if g:
            cnt = {}
            for x in g:
                for aid in inverted.get(x, ()):
                    cnt[aid] = cnt.get(aid, 0) + 1
            if cnt:
                aid, h = max(cnt.items(), key=lambda kv: kv[1])
                row["best_orig"] = aid
                row["best_hits"] = h
                row["rate"] = round(h / len(g), 3)
        results.append(row)

    dup = [r for r in results if r["rate"] >= DUP_THRESHOLD]
    uniq = [r for r in results if r["rate"] < DUP_THRESHOLD]
    log("  比对完成: 总计 %d 篇" % len(results))
    log("    与原博客重复（命中率>=%.2f）: %d 篇" % (DUP_THRESHOLD, len(dup)))
    log("    原博客没有的独立内容        : %d 篇" % len(uniq))

    # 3) 独立内容 与 缺失清单 做标题匹配
    todo = json.load(io.open(TODO_FILE, encoding="utf-8"))
    todo_pairs = [(norm_title(x["list_title"]), x) for x in todo["items"]]

    cls_a, cls_b = [], []
    for r in uniq:
        probe = norm_title(r["title"])
        hit = None
        if probe and len(probe) >= 4:
            for tn, x in todo_pairs:
                if len(tn) >= 4 and (probe in tn or tn in probe):
                    hit = x
                    break
        if hit:
            r["todo_title"] = hit["list_title"]
            r["todo_date"] = hit["list_date"][:10]
            cls_a.append(r)
        else:
            cls_b.append(r)

    log("-" * 74)
    log("独立内容细分：")
    log("  A 类（补上了原博客缺失清单中的文章）: %d 篇" % len(cls_a))
    log("  B 类（原博客清单中也没有的独立内容）: %d 篇" % len(cls_b))

    log("")
    log("【A 类明细】")
    for r in cls_a:
        log("  %s" % r["title"][:44])
        log("     <=> 原博客缺失: %s (%s) | 指纹命中率 %.3f"
            % (r["todo_title"][:36], r["todo_date"], r["rate"]))

    log("")
    log("【B 类抽样 25 篇（原博客清单里也没有的）】")
    for r in sorted(cls_b, key=lambda x: x.get("orig_pub_time") or x["pub_time"])[:25]:
        log("  %s | %s | %s" % ((r.get("orig_pub_time") or r["pub_time"])[:10],
                                r["title"][:40], r["supp_id"]))

    log("")
    log("【重复样例（命中率最高 10 篇，说明确实是同一篇）】")
    for r in sorted(dup, key=lambda x: -x["rate"])[:10]:
        log("  %s" % r["title"][:40])
        log("     <=> 原博客 %s (命中率 %.3f, %d/%d)"
            % (r["best_orig"], r["rate"], r["best_hits"], r["gram_cnt"]))

    with io.open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({"dup": dup, "unique": uniq, "class_a": cls_a, "class_b": cls_b,
                   "params": {"n": N, "step": STEP, "dup_threshold": DUP_THRESHOLD}},
                  f, ensure_ascii=False, indent=1)
    with io.open(OUT_TXT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log("")
    log("结果: %s" % OUT_JSON)
    log("报告: %s" % OUT_TXT)


if __name__ == "__main__":
    main()