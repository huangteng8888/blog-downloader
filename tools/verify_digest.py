# -*- coding: utf-8 -*-
"""字节级校验（v2）：把 archive.org 快照原始字节的 SHA1(base32) 与 CDX 索引 digest 比对。

两种模式：
  默认（离线） 扫描本地 raw/*.gz，核对每个存档文件与 front-matter 记录的
               raw_sha1_b32 是否一致（证明归档环节未损坏字节），并对
               JSON 信封产物给出解包后的 digest，供与 CDX 进一步比对。
              零网络请求，可随时运行。
  --cdx N      在线抽样：取 N 篇（默认 3，间隔 15 秒）向 CDX 查询 digest，
               再经反代取字节，逐一尝试 原始字节 / JSON.content(utf-8) /
               JSON.content(latin1) 三种形态比对。

修复记录（2026-10-24）：原版只比对网络取回字节且硬编码 2 个样本；
JSON 信封未统一解包会导致 SHA1 必然不匹配（误判为"内容被篡改"）。
"""
import io, os, re, sys, time, json, gzip, glob, base64, hashlib
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import wayback_resume as wr  # noqa: E402

OUT = os.path.join(ROOT, "output", "1216826604_wayback")
POSTS = os.path.join(OUT, "posts")
RAW = os.path.join(OUT, "raw")
INTEG = os.path.join(OUT, "integrity")
CDX = os.path.join(ROOT, "temp", "wayback_cdx2.json")
REPORT = os.path.join(INTEG, "digest_verify_local.tsv")


def b32sha1(b):
    return base64.b32encode(hashlib.sha1(b).digest()).decode("ascii")


def fm_of(path, n=900):
    t = io.open(path, encoding="utf-8", errors="ignore").read(n)
    return dict((m.group(1), m.group(2).strip())
                for m in re.finditer(r"^([a-z0-9_]+):\s*(.*?)\s*$", t, re.M))


# ---------------------------------------------------------------- 离线自证
def offline():
    rows = []
    match = mismatch = wrapped = nohash = 0
    for fp in sorted(glob.glob(os.path.join(RAW, "*.gz"))):
        aid = os.path.basename(fp).split(".")[0]
        mp = os.path.join(POSTS, aid + ".md")
        if not os.path.exists(mp):
            continue
        fm = fm_of(mp)
        rec = fm.get("raw_sha1_b32", "")
        try:
            b = gzip.open(fp, "rb").read()
        except Exception as e:
            rows.append((aid, "READ_ERROR", "", "", "", str(e)[:50]))
            continue
        calc = b32sha1(b)
        if not rec:
            nohash += 1
            rows.append((aid, "NO_RECORDED_HASH", calc, "", "", ""))
            continue
        w = 1 if fm.get("raw_wrapped") == "1" else 0
        if calc == rec:
            match += 1
            if w:
                wrapped += 1
                cands = wr.unwrap_delivered(b)
                un = b32sha1(cands[1][1]) if len(cands) > 1 else ""
                rows.append((aid, "OK(Wrapped)", calc, rec, un,
                             "信封已解包；比对 CDX 需用解包后 digest"))
            else:
                rows.append((aid, "OK", calc, rec, "", ""))
        else:
            mismatch += 1
            rows.append((aid, "MISMATCH", calc, rec, "", "归档字节与记录不符"))

    if not os.path.isdir(INTEG):
        os.makedirs(INTEG)
    with io.open(REPORT, "w", encoding="utf-8", newline="") as f:
        f.write("# 生成时间 %s（离线校验，零网络请求）\n"
                % time.strftime("%Y-%m-%d %H:%M:%S"))
        f.write("article_id\tstatus\tcalc_sha1_b32\trecorded_sha1_b32"
                "\tunwrapped_sha1_b32\tnote\n")
        for r in rows:
            f.write("\t".join(r) + "\n")

    print("### 离线字节校验（raw/ 本地存档）")
    print("  存档文件      : %d" % len(rows))
    print("  与记录一致    : %d" % match)
    print("    └ 其中 JSON 信封 : %d" % wrapped)
    print("  **与记录不符** : %d  (须为 0)" % mismatch)
    print("  无记录 hash   : %d" % nohash)
    print("  报告: %s" % REPORT)
    return mismatch


# ---------------------------------------------------------------- 在线 CDX
def cdx_digest(url, ts, tmpl):
    q = ("https://web.archive.org/cdx/search/cdx?url=%s&timestamp=%s&output=json"
         "&filter=statuscode:200&limit=1" % (urllib.parse.quote(url), ts))
    enc = urllib.parse.quote(q, safe="")
    req = urllib.request.Request(tmpl.format(raw=q, enc=enc),
                                 headers={"User-Agent": wr.UA})
    raw = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
    try:
        j = json.loads(raw)
        if isinstance(j, dict):
            j = json.loads(j.get("content", "[]"))
    except Exception:
        return None
    if isinstance(j, list) and len(j) > 1:
        return dict(zip(j[0], j[1]))
    return None


def online(n_sample):
    cdx_index = json.load(io.open(CDX, encoding="utf-8")).get("items", {})
    have = sorted(glob.glob(os.path.join(RAW, "*.gz")))
    picked = []
    for fp in have:
        aid = os.path.basename(fp).split(".")[0]
        if aid in cdx_index:
            picked.append((aid, fp))
        if len(picked) >= n_sample:
            break
    print("\n### 在线 CDX 抽样比对（%d 篇，间隔 15 秒，遵守限速）" % len(picked))
    ok = 0
    for i, (aid, fp) in enumerate(picked):
        v = cdx_index[aid]
        node, tmpl = wr.NODES[(i + 2) % len(wr.NODES)]
        if i:
            time.sleep(15)
        print("=" * 62)
        print("文章 %s  (%s)" % (aid, v.get("url", "")))
        try:
            meta = cdx_digest(v["url"], v["timestamp"], tmpl)
        except Exception as e:
            print("  CDX 查询失败: %s" % str(e)[:70])
            continue
        if not meta:
            print("  CDX 无返回")
            continue
        digest = meta.get("digest", "")
        print("  索引 digest : %s" % digest)
        target = "https://web.archive.org/web/%sid_/%s" % (v["timestamp"], v["url"])
        enc = urllib.parse.quote(target, safe="")
        try:
            req = urllib.request.Request(tmpl.format(raw=target, enc=enc),
                                         headers={"User-Agent": wr.UA})
            raw = urllib.request.urlopen(req, timeout=45).read()
        except Exception as e:
            print("  取字节失败: %s" % str(e)[:70])
            continue
        cands = wr.unwrap_delivered(raw)
        hit = None
        for label, b in cands:
            if b32sha1(b) == digest:
                hit = label
                break
        if hit:
            ok += 1
        print("  %d 字节 | 形态尝试: %s" % (len(raw), " / ".join(l for l, _ in cands)))
        print("  结论: %s" % ("✅ 与索引 digest 一致（%s）" % hit if hit
                              else "❌ 三种形态均不一致（反代可能改动字节，需直连复核）"))
    print("\n在线抽样一致: %d / %d" % (ok, len(picked)))


if __name__ == "__main__":
    n = 0
    if "--cdx" in sys.argv:
        i = sys.argv.index("--cdx")
        n = int(sys.argv[i + 1]) if len(sys.argv) > i + 1 else 3
    bad = offline()
    if n:
        online(n)
    sys.exit(1 if bad else 0)