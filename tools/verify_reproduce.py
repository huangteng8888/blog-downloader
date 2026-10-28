# -*- coding: utf-8 -*-
"""抽样验证：已落盘文本能否从 archive.org 快照逐字复现（不复现即说明存在改写或串号）。
另查 CDX 索引是否提供 digest（字节级校验的可行性探针）。

只读本地文件；网络请求遵守 15 秒/次间隔，与抓取任务共用节点但互不影响落盘。
"""
import io, os, re, sys, json, time, glob, random, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import wayback_resume as wr  # noqa: E402

POSTS = os.path.join(ROOT, "output", "1216826604_wayback", "posts")
CDX = os.path.join(ROOT, "temp", "wayback_cdx2.json")
cdx = json.load(io.open(CDX, encoding="utf-8")).get("items", {})

files = sorted(glob.glob(os.path.join(POSTS, "*.md")))
random.seed(20261024)
# 抽样：新产物 3 篇 + 旧版产物 2 篇（无 title/source_url）+ 图文贴 1 篇
new, legacy, photo = [], [], []
for f in files:
    t = io.open(f, encoding="utf-8", errors="ignore").read()
    if "content_type: photo_only" in t:
        photo.append(f)
    elif "source_url:" in t:
        new.append(f)
    else:
        legacy.append(f)

samples = []
samples += random.sample(new, min(3, len(new)))
samples += random.sample(legacy, min(2, len(legacy)))
samples += random.sample(photo, min(1, len(photo)))

print("### 抽样验证：文本可复现性（%d 篇）" % len(samples))
print("说明：重新从 archive.org 同一快照抓取并重跑抽取器，与已落盘文本逐字比对")
print("")

n = 0
results = []
for f in samples:
    aid = os.path.basename(f)[:-3]
    t = io.open(f, encoding="utf-8", errors="ignore").read()
    fm = {k: v.strip() for k, v in re.findall(r"^([a-z_]+):\s*(.*)$", t[:800], re.M)}
    stored_body = t.split("---", 2)[-1].strip()
    v = cdx.get(aid)
    if not v:
        print("- %s 不在 CDX 清单，跳过" % aid)
        continue
    if n:
        time.sleep(15)
    node, tmpl = wr.NODES[n % len(wr.NODES)]
    n += 1
    idx = n
    try:
        html = wr.fetch_one(node, tmpl, v["timestamp"], v["url"])
    except Exception as e:
        print("[%d] %s 抓取失败: %s" % (idx, aid, str(e)[:60]))
        results.append((aid, "fetch_fail"))
        continue
    if not html:
        print("[%d] %s 快照返回空（节点 %s）" % (idx, aid, node))
        results.append((aid, "empty"))
        continue
    status, p = wr.parse_article(html)
    fresh_body = (p or {}).get("content", "")
    fresh_title = (p or {}).get("title", "")
    fresh_time = (p or {}).get("pub_time", "")
    same_body = (fresh_body == stored_body)
    same_title = (not fm.get("title")) or (fresh_title == fm.get("title"))
    same_time = (not fm.get("pub_time")) or (fresh_time == fm.get("pub_time"))
    tag = "逐字一致" if (same_body and same_title and same_time) else "存在差异"
    results.append((aid, tag))
    print("[%d] %s  节点=%s  类型=%s" % (idx, aid, node, "旧版产物" if not fm.get("source_url") else (
        "图文贴" if "photo_only" in t else "新版产物")))
    print("     快照 %s | %s" % (v["timestamp"], v["url"]))
    print("     正文: 落盘 %d 字 / 复现 %d 字 → %s" % (
        len(stored_body), len(fresh_body), "一致" if same_body else "不一致"))
    print("     标题: %s | 时间: %s" % (
        "一致" if same_title else "不一致(%s vs %s)" % (fm.get("title"), fresh_title),
        "一致" if same_time else "不一致(%s vs %s)" % (fm.get("pub_time"), fresh_time)))
    if not same_body:
        i = 0
        while i < min(len(stored_body), len(fresh_body)) and stored_body[i] == fresh_body[i]:
            i += 1
        print("     首个差异位置 %d：" % i)
        print("       落盘: %r" % stored_body[max(0, i - 30):i + 50])
        print("       复现: %r" % fresh_body[max(0, i - 30):i + 50])

print("\n### 结论")
ok = [r for r in results if r[1] == "逐字一致"]
print("完全一致 %d / %d 篇" % (len(ok), len(results)))
for aid, tag in results:
    if tag != "逐字一致":
        print("  需人工复核: %s → %s" % (aid, tag))

# CDX digest 可行性探针
print("\n### CDX 索引字节级校验可行性探针")
probe = samples[:2]
for i, f in enumerate(probe):
    aid = os.path.basename(f)[:-3]
    v = cdx.get(aid)
    if not v:
        continue
    if i:
        time.sleep(15)
    node, tmpl = wr.NODES[(n + i) % len(wr.NODES)]
    q = ("https://web.archive.org/cdx/search/cdx?url=%s&timestamp=%s&output=json"
         "&filter=statuscode:200&limit=1" % (urllib.parse.quote(v["url"]), v["timestamp"]))
    enc = urllib.parse.quote(q, safe="")
    pu = tmpl.format(raw=q, enc=enc)
    try:
        req = urllib.request.Request(pu, headers={"User-Agent": wr.UA})
        raw = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
        j = json.loads(raw)
        if isinstance(j, dict):
            raw = j.get("content", "")
            j = json.loads(raw)
        print("- %s 经节点 %s 查询 CDX: %s" % (aid, node, json.dumps(j, ensure_ascii=False)[:400]))
    except Exception as e:
        print("- %s CDX 查询失败（节点 %s）: %s" % (aid, node, str(e)[:80]))