# -*- coding: utf-8 -*-
"""wu2198 补抓产出的真实性与完整性审计（只读文章内容，不修改任何 .md）。

校验维度：
  A 溯源字段完整性          B 身份三向一致（文件名 / front-matter / source_url / CDX 清单）
  C 作者归属（uid=1216826604）D 来源域名（仅 sina 官方博客域）
  E 快照时间戳与 CDX 索引一致  F 快照时间不早于发布时间
  G 正文重复检测（转载/重复内容）H 全局 sha256 防篡改清单
  I 文章 id 序列与发布时间单调性
"""
import io, os, re, json, glob, time, hashlib, collections

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "output", "1216826604_wayback")
POSTS = os.path.join(OUT, "posts")
CDX = os.path.join(ROOT, "temp", "wayback_cdx2.json")
INTEG = os.path.join(OUT, "integrity")
UID = "1216826604"
OK_HOSTS = ("blog.sina.com.cn", "sina.com.cn")

cdx = json.load(io.open(CDX, encoding="utf-8")).get("items", {})
files = sorted(glob.glob(os.path.join(POSTS, "*.md")))

stats = collections.Counter()
problems = collections.defaultdict(list)
seen_body = {}
dups = []
manifest = []

for f in files:
    base = os.path.basename(f)[:-3]
    raw = io.open(f, "rb").read()
    sha = hashlib.sha256(raw).hexdigest()
    manifest.append((base, len(raw), sha))
    t = raw.decode("utf-8", "ignore")
    fm = {k: v.strip() for k, v in re.findall(r"^([a-z_]+):\s*(.*)$", t[:800], re.M)}
    body = t.split("---", 2)[-1].strip() if t.count("---") >= 2 else ""

    # A 字段完整性
    missing = [k for k in ("article_id", "title", "pub_time", "source_url") if not fm.get(k)]
    if missing:
        stats["A_缺字段"] += 1
        problems["A_缺字段"].append((base, ",".join(missing)))
    if not fm.get("snapshot_ts"):
        stats["A_无时间戳"] += 1
    if not fm.get("origin"):
        stats["A_旧版产物(无origin/stage)"] += 1

    # B 身份三向一致
    aid = fm.get("article_id", "")
    if aid != base:
        stats["B_文件名与article_id不符"] += 1
        problems["B"].append((base, aid))
    url = fm.get("source_url", "")
    in_url = re.search(r"/blog_(\w+)\.html", url)
    if in_url and in_url.group(1) != aid.replace("blog_", ""):
        stats["B_url内id不符"] += 1
        problems["B_url"].append((base, in_url.group(1)))
    if base not in cdx:
        stats["B_不在CDX清单"] += 1
        problems["B_cdx"].append((base, ""))

    # C 作者归属
    if in_url and not in_url.group(1).startswith("48874cec"):
        stats["C_非本博客id"] += 1
        problems["C"].append((base, in_url.group(1)))

    # D 来源域名
    host = re.sub(r"^https?://", "", url).split("/")[0].split(":")[0]
    if host and host not in OK_HOSTS:
        stats["D_非sina域"] += 1
        problems["D"].append((base, host))

    # E 快照时间戳与 CDX 一致
    ts = fm.get("snapshot_ts", "")
    c = cdx.get(base)
    if c:
        if ts and ts != c.get("timestamp"):
            stats["E_时间戳与CDX不符"] += 1
            problems["E"].append((base, ts, c.get("timestamp")))
        if url and url != c.get("url"):
            stats["E_url与CDX不符"] += 1
            problems["E_url"].append((base, url, c.get("url")))

    # F 快照不早于发布
    pt, st = fm.get("pub_time", ""), fm.get("snapshot_ts", "")
    if pt and st and len(st) >= 8:
        try:
            p = time.mktime(time.strptime(pt[:10], "%Y-%m-%d"))
            s = time.mktime(time.strptime(st[:8], "%Y%m%d"))
            if s < p:
                stats["F_快照早于发布"] += 1
                problems["F"].append((base, pt, st))
        except Exception:
            stats["F_时间解析失败"] += 1

    # G 正文重复
    if body:
        h = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
        if h in seen_body:
            dups.append((seen_body[h], base, fm.get("title", "")[:20], len(body)))
        else:
            seen_body[h] = base

    # I id 序列（hex 后缀）与时间：统一在文末处理

os.makedirs(INTEG, exist_ok=True)
mp = os.path.join(INTEG, "manifest_sha256.tsv")
with io.open(mp, "w", encoding="utf-8") as fh:
    fh.write("# article_id\tbytes\tsha256\t生成时间 %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
    for base, n, sha in manifest:
        fh.write("%s\t%d\t%s\n" % (base, n, sha))

print("### 审计范围")
print("文章文件 %d 篇 | CDX 索引 %d 条 | 审计时间 %s" % (
    len(files), len(cdx), time.strftime("%Y-%m-%d %H:%M:%S")))
print("\n### 计数结果")
for k in sorted(stats):
    print("  %-24s %d" % (k, stats[k]))
zero = [k for k in ("B_文件名与article_id不符", "B_url内id不符", "B_不在CDX清单",
                    "C_非本博客id", "D_非sina域", "E_时间戳与CDX不符", "E_url与CDX不符",
                    "F_快照早于发布") if stats[k] == 0]
print("\n### 关键一致性检查全部为 0 的项：")
for k in zero:
    print("  [通过] %s" % k)
if not zero:
    print("  无")
print("\n### 正文重复（不同 article_id 但正文哈希相同）")
print("重复组 %d 个" % len(dups))
for a, b, title, n in dups[:10]:
    print("  %s == %s  《%s》 %d 字" % (a, b, title, n))
print("\n### 问题明细（最多各列 5 条）")
for k, v in problems.items():
    if v:
        print("  [%s] %d 条，样例: %s" % (k, len(v), v[:5]))

# I id 序列与时间单调性
seq = []
for f in files:
    base = os.path.basename(f)[:-3]
    t = io.open(f, encoding="utf-8", errors="ignore").read(800)
    fm = {k: v.strip() for k, v in re.findall(r"^([a-z_]+):\s*(.*)$", t, re.M)}
    pt = fm.get("pub_time", "")
    m = re.match(r"^(?:blog_)?48874cec(01000[0-9a-z]+)$", base)
    if m and pt:
        try:
            seq.append((int(m.group(1), 36), pt[:19], base))
        except Exception:
            pass
seq.sort()
inv = 0
for i in range(1, len(seq)):
    if seq[i][1] < seq[i - 1][1]:
        inv += 1
print("\n### id 序列与发布时间单调性")
print("可比对 %d 篇，相邻倒挂 %d 处（%.2f%%）" % (
    len(seq), inv, inv * 100.0 / max(len(seq) - 1, 1)))
if inv:
    print("样例倒挂:")
    for i in range(1, len(seq)):
        if seq[i][1] < seq[i - 1][1]:
            print("  %s(%s) → %s(%s)" % (seq[i - 1][2], seq[i - 1][1], seq[i][2], seq[i][1]))
            if i > 3:
                break
print("\n清单已写入: %s" % mp)