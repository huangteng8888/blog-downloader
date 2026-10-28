# -*- coding: utf-8 -*-
"""字节级校验：把 archive.org 快照原始字节的 SHA1(base32) 与 CDX 索引 digest 比对。
这是最强的证据链：digest 由 archive.org 在抓取时计算并写入索引，任何环节改动字节都会导致不一致。
注意：必须取原始字节（不 decode），且优先请求 id_ 原始版本。
"""
import io, os, re, sys, json, time, base64, hashlib, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import wayback_resume as wr  # noqa: E402

cdx = json.load(io.open(os.path.join(ROOT, "temp", "wayback_cdx2.json"), encoding="utf-8")).get("items", {})

SAMPLES = ["48874cec01007xdz", "48874cec01000fal"]
DUMMY = "https://web.archive.org/"


def cdx_digest(url, ts, node, tmpl):
    q = ("https://web.archive.org/cdx/search/cdx?url=%s&timestamp=%s&output=json"
         "&filter=statuscode:200&limit=1" % (urllib.parse.quote(url), ts))
    enc = urllib.parse.quote(q, safe="")
    req = urllib.request.Request(tmpl.format(raw=q, enc=enc), headers={"User-Agent": wr.UA})
    raw = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
    try:
        j = json.loads(raw)
        if isinstance(j, dict):
            j = json.loads(j.get("content", "[]"))
    except Exception:
        return None
    if isinstance(j, list) and len(j) > 1:
        head, row = j[0], j[1]
        return dict(zip(head, row))
    return None


def fetch_bytes(target, tmpl):
    enc = urllib.parse.quote(target, safe="")
    req = urllib.request.Request(tmpl.format(raw=target, enc=enc),
                                 headers={"User-Agent": wr.UA})
    with urllib.request.urlopen(req, timeout=45) as r:
        return r.read()


def b32sha1(b):
    return base64.b32encode(hashlib.sha1(b).digest()).decode("ascii")


for i, aid in enumerate(SAMPLES):
    v = cdx.get(aid)
    node, tmpl = wr.NODES[(i + 2) % len(wr.NODES)]
    if i:
        time.sleep(15)
    print("=" * 66)
    print("文章 %s" % aid)
    try:
        meta = cdx_digest(v["url"], v["timestamp"], node, tmpl)
    except Exception as e:
        print("  CDX 查询失败: %s" % str(e)[:80])
        continue
    if not meta:
        print("  CDX 无返回")
        continue
    digest = meta.get("digest")
    print("  索引 digest : %s  (%d 字节原文)" % (digest, int(meta.get("length", 0))))

    for suffix, label in (("id_", "原始版 id_"), ("", "渲染版")):
        target = "https://web.archive.org/web/%s%s/%s" % (v["timestamp"], suffix, v["url"])
        try:
            raw = fetch_bytes(target, tmpl)
        except Exception as e:
            print("  %s 经节点 %s 取字节失败: %s" % (label, node, str(e)[:70]))
            continue
        cands = {"原始字节": raw}
        if raw[:1] in (b"{", b"["):
            try:
                j = json.loads(raw.decode("utf-8", "ignore"))
                if isinstance(j, dict) and "content" in j:
                    cands["JSON.content(utf-8 重编码)"] = j["content"].encode("utf-8")
                    cands["JSON.content(latin1 还原)"] = j["content"].encode("latin-1", "ignore")
            except Exception:
                pass
        hit = None
        for k, b in cands.items():
            if b32sha1(b) == digest:
                hit = k
                break
        print("  %s 经节点 %s：%d 字节 | SHA1(b32)=%s → %s" % (
            label, node, len(raw), b32sha1(raw),
            "✅ 与索引 digest 完全一致（%s）" % hit if hit else "❌ 不一致"))
    if i == 0:
        print("  说明：不一致通常源于反代对内容做了编码/包装，不等于内容被改；需直连复核。")