# -*- coding: utf-8 -*-
"""
把心明禅师转载数据整理成两个独立目录：

  confirmed/    —— 有分钟级时间证据，可确认对应原博客缺失文章的条目（含重复转载副本）
  unverified/   —— 其余转载（第三方转载、标题已被改写，无法与原博客对照）

判定说明：
    同一篇原文若被转载多次，各副本的 orig_pub_time 相同，
    因此用"原文时间戳"而不是单篇 id 来归类，才能把副本一并归入 confirmed。

原 posts/ 保留为全部转载的原始全集，不改动。

用法：python tools/split_supplement.py
"""
import io
import json
import os
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(ROOT, "output", "1216826604_supplement")
SRC_POSTS = os.path.join(BASE, "posts")
SRC_INDEX = os.path.join(BASE, "index.json")
CONFIRMED_JSON = os.path.join(ROOT, "temp", "confirmed_supplements.json")

CONF_DIR = os.path.join(BASE, "confirmed")
UNV_DIR = os.path.join(BASE, "unverified")


def load(path):
    return json.load(io.open(path, encoding="utf-8"))


def fresh(d):
    """清空并重建目录"""
    if os.path.isdir(d):
        shutil.rmtree(d)
    os.makedirs(d)


def main():
    idx = load(SRC_INDEX)
    posts = {p["article_id"]: p for p in idx["posts"]}
    conf = load(CONFIRMED_JSON)

    # 以"原文发布时间戳"为键，才能把重复转载的副本一起归类
    conf_by_time = {}
    for it in conf["items"]:
        conf_by_time.setdefault(it["orig_pub_time"], it)

    print("转载总数            : %d" % len(posts))
    print("可确认的原文时间戳  : %d 个" % len(conf_by_time))

    fresh(CONF_DIR)
    fresh(UNV_DIR)
    os.makedirs(os.path.join(CONF_DIR, "posts"))
    os.makedirs(os.path.join(UNV_DIR, "posts"))

    conf_list, unv_list = [], []
    for aid, p in posts.items():
        src = os.path.join(SRC_POSTS, aid + ".md")
        if not os.path.exists(src):
            continue
        ot = (p.get("orig_pub_time") or "").strip()
        if ot and ot in conf_by_time:
            shutil.copy2(src, os.path.join(CONF_DIR, "posts", aid + ".md"))
            e = conf_by_time[ot]
            conf_list.append({
                "article_id": aid,
                "reprint_title": p.get("title", ""),
                "orig_title": e["orig_title"],
                "orig_list_time": e["orig_list_time"],
                "orig_pub_time": ot,
                "evidence": "原文时间戳与原博客缺失清单精确到分钟一致",
                "source_url": p.get("source_url", ""),
                "content_chars": p.get("content_chars", 0),
                "note": "第三方转载版本；标题已被转载者改写，正文可能与原文有出入",
            })
        else:
            shutil.copy2(src, os.path.join(UNV_DIR, "posts", aid + ".md"))
            cat = (p.get("category") or "").replace("\n", " ").replace("\t", " ").strip()
            unv_list.append({
                "article_id": aid,
                "title": p.get("title", ""),
                "pub_time": p.get("pub_time", ""),
                "orig_pub_time": ot,
                "category": cat,
                "relevance": "high" if "2198" in cat else "other",
                "source_url": p.get("source_url", ""),
                "content_chars": p.get("content_chars", 0),
                "reason": ("正文未保留原文时间戳，无法与原博客缺失清单对齐"
                           if not ot else "有原文时间戳，但与缺失清单对不上"),
                "note": "第三方转载版本，标题已改写；已核验不与原博客 2404 篇重复",
            })

    # 归并成"独立文章"视角
    by_orig = {}
    for x in conf_list:
        by_orig.setdefault(x["orig_pub_time"], []).append(x)

    conf_list.sort(key=lambda x: (x["orig_pub_time"], x["reprint_title"]))
    unv_list.sort(key=lambda x: (x.get("orig_pub_time") or x.get("pub_time") or ""))

    with io.open(os.path.join(CONF_DIR, "index.json"), "w", encoding="utf-8") as f:
        json.dump({
            "scope": "可确认补充的条目（含重复转载副本）",
            "method": "转载正文保留的 orig_pub_time[:16] == 原博客缺失清单 list_date[:16]",
            "unique_articles": len(by_orig),
            "files": len(conf_list),
            "items": conf_list,
        }, f, ensure_ascii=False, indent=1)

    with io.open(os.path.join(UNV_DIR, "index.json"), "w", encoding="utf-8") as f:
        json.dump({
            "scope": "无法与原博客对照的转载内容",
            "total": len(unv_list),
            "high_relevance": sum(1 for x in unv_list if x["relevance"] == "high"),
            "items": unv_list,
        }, f, ensure_ascii=False, indent=1)

    # README
    with io.open(os.path.join(CONF_DIR, "README.md"), "w", encoding="utf-8") as f:
        f.write("# confirmed —— 可确认补充的条目\n\n")
        f.write("**%d 篇独立文章**（%d 个转载文件，含重复转载副本）。\n\n"
                % (len(by_orig), len(conf_list)))
        f.write("判定依据：转载正文中保留的原文发布时间，与原博客缺失清单的记录时间"
                "**精确到分钟一致**。\n\n")
        f.write("| # | 原博客标题 | 原博客时间 | 转载标题 | 副本 |\n|---|---|---|---|---|\n")
        for i, (t, rows) in enumerate(sorted(by_orig.items()), 1):
            f.write("| %d | %s | %s | %s | %d |\n"
                    % (i, rows[0]["orig_title"], rows[0]["orig_list_time"],
                       rows[0]["reprint_title"], len(rows)))
        f.write("\n> 内容为第三方转载版本，标题已被改写，正文可能与原文有出入。\n"
                "> 完整证据见 `index.json` 与 `../CONFIRMED.md`。\n")

    with io.open(os.path.join(UNV_DIR, "README.md"), "w", encoding="utf-8") as f:
        f.write("# unverified —— 无法与原博客对照的转载内容\n\n")
        f.write("共 **%d 篇**，其中转载分类为 `wu2198` 的 **%d 篇**。\n\n"
                % (len(unv_list), sum(1 for x in unv_list if x["relevance"] == "high")))
        f.write("## 为什么归到这里\n\n")
        f.write("- **%d 篇**正文没有保留原文发布时间，无法与缺失清单做时间对齐；\n"
                % sum(1 for x in unv_list if not x["orig_pub_time"]))
        f.write("- **%d 篇**有原文时间戳，但与缺失清单对不上"
                "（原文可能不在列表采样范围内，或已被删除）。\n\n"
                % sum(1 for x in unv_list if x["orig_pub_time"]))
        f.write("## 注意事项\n\n")
        f.write("1. 均为第三方转载版本，**标题被改写**、正文可能与原文有出入；\n")
        f.write("2. 已用正文指纹核验：**不与原博客已采集的 2404 篇重复**；\n")
        f.write("3. 建议单独存放，不要与原博客数据混库。\n")

    print("-" * 66)
    print("confirmed/  : %d 个文件 / %d 篇独立文章" % (len(conf_list), len(by_orig)))
    print("unverified/ : %d 篇 (其中 wu2198 分类 %d 篇)"
          % (len(unv_list), sum(1 for x in unv_list if x["relevance"] == "high")))
    print("原 posts/   : %d 篇（保留为全集）" % len(posts))


if __name__ == "__main__":
    main()