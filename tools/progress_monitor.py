# -*- coding: utf-8 -*-
"""
wu2198 补抓进度可视化监控（零网络开销：只读本地文件）

用法：
    python tools/progress_monitor.py            # 刷新一次仪表盘 + 控制台摘要
    python tools/progress_monitor.py --watch 60 # 每 60 秒刷新，常驻查看

产物：output/1216826604_wayback/dashboard.html（页面每 30 秒自动刷新）
"""
import argparse
import glob
import io
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "output", "1216826604_wayback")
POSTS = os.path.join(OUT, "posts")
LOG = os.path.join(OUT, "progress.log")
CDX = os.path.join(ROOT, "temp", "wayback_cdx2.json")
HTML = os.path.join(OUT, "dashboard.html")
SNAP = os.path.join(OUT, "progress_snapshot.txt")


def load_json(p, d=None):
    try:
        with io.open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {} if d is None else d


def read_log():
    try:
        return io.open(LOG, encoding="utf-8").read().split("\n")
    except Exception:
        return []


def collect():
    d = {}
    total = len(load_json(CDX, {}).get("items", {}))
    files = glob.glob(os.path.join(POSTS, "*.md"))
    d["total"] = total
    d["done"] = len(files)
    d["remaining"] = max(total - d["done"], 0)

    now = time.time()
    mt = sorted(os.path.getmtime(f) for f in files)
    for win, key in ((600, "r10"), (1800, "r30"), (3600, "r60"), (86400, "rday")):
        sel = [t for t in mt if now - t <= win]
        d[key] = (len(sel) - 1) / ((sel[-1] - sel[0]) / 60.0) if len(sel) > 1 else 0.0
    d["today"] = len([t for t in mt if now - t <= 86400])
    rate = d["r60"] or d["r30"] or d["r10"]
    d["eta_min"] = d["remaining"] / rate if rate > 0 else 0
    d["eta_at"] = time.strftime("%m-%d %H:%M", time.localtime(now + d["eta_min"] * 60)) if rate else "--"

    # 暂存区实际构成（口径准确；日志中的"本轮"汇总仅覆盖已结束轮次）
    photo_all = 0
    empty_all = 0
    for f in files:
        try:
            head = io.open(f, encoding="utf-8", errors="ignore").read(600)
        except Exception:
            continue
        if "content_type: photo_only" in head:
            photo_all += 1
        elif "content_type: empty" in head or "body_chars: 0" in head:
            empty_all += 1
    d["photo_all"] = photo_all
    d["empty_all"] = empty_all
    d["text_all"] = max(len(files) - photo_all - empty_all, 0)
    # 日志聚合
    lines = read_log()
    d["log_lines"] = len(lines)
    d["last_line"] = lines[-2] if len(lines) > 1 and not lines[-1].strip() else (lines[-1] if lines else "")
    ok = photo = skip = trans = 0
    for l in lines:
        m = re.search(r"本轮：成功 (\d+) \| 纯图片 (\d+) \| 永久跳过 (\d+) \| 临时失败 (\d+)", l)
        if m:
            ok += int(m.group(1)); photo += int(m.group(2))
            skip += int(m.group(3)); trans += int(m.group(4))
    d["rounds"] = {"ok": ok, "photo": photo, "skip": skip, "transient": trans}
    try:
        lt = os.path.getmtime(LOG)
        d["log_age"] = now - lt
    except Exception:
        d["log_age"] = 1e9

    st = load_json(os.path.join(OUT, "skip_permanent.json"))
    d["permanent"] = st
    d["transient"] = load_json(os.path.join(OUT, "failed_transient.json"))
    d["warnings"] = load_json(os.path.join(OUT, "warnings.json"))

    # 最近捕获
    recent = []
    for f in sorted(files, key=os.path.getmtime, reverse=True)[:12]:
        t = io.open(f, encoding="utf-8", errors="ignore").read()
        fm = dict(re.findall(r"^(\w+):\s*(.*)$", t[:600], re.M))
        body = t.split("---", 2)[-1].strip() if t.count("---") >= 2 else ""
        recent.append({
            "id": fm.get("article_id", os.path.basename(f)[:-3]),
            "title": fm.get("title", "") or "(无标题)",
            "time": fm.get("pub_time", ""),
            "chars": len(body),
            "flag": "图文贴" if fm.get("content_type") == "photo_only" else "",
            "at": time.strftime("%H:%M:%S", time.localtime(os.path.getmtime(f))),
        })
    d["recent"] = recent

    # 近 12 小时逐小时产出
    hours = []
    for h in range(11, -1, -1):
        lo = now - (h + 1) * 3600
        hi = now - h * 3600
        c = len([t for t in mt if lo < t <= hi])
        hours.append((time.strftime("%H", time.localtime(hi)), c))
    d["hours"] = hours
    return d


def render(d):
    total = max(d["total"], 1)
    pct = d["done"] * 100.0 / total
    live = "运行中" if d["log_age"] < 300 else ("可能已停止/受阻（日志 %d 分钟未更新）" % int(d["log_age"] // 60))
    live_color = "#16a34a" if d["log_age"] < 300 else "#dc2626"
    hmax = max([c for _, c in d["hours"]] + [1])

    bars = "".join(
        '<div class="bar"><div class="barfill" style="height:%dpx" title="%s 点 %d 篇"></div>'
        '<span>%s</span></div>' % (max(int(c / float(hmax) * 90), 2), h, c, h)
        for h, c in d["hours"])

    rows = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td class='num'>%s</td><td>%s</td><td>%s</td></tr>"
        % (r["at"], r["id"], r["title"][:40], r["chars"], r["time"], r["flag"])
        for r in d["recent"])

    def _fmt(k, v):
        if isinstance(v, dict):
            return "<li><code>%s</code> — %s（%s）</li>" % (
                k, v.get("reason", "?"), v.get("at", "")[:16])
        return "<li><code>%s</code> — %s</li>" % (k, v)

    perm = "".join(_fmt(k, v) for k, v in list(d["permanent"].items())[:20]) or "<li>暂无</li>"

    return """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="30">
<title>wu2198 补抓进度</title>
<style>
 body{{font-family:"Microsoft YaHei",Arial,sans-serif;background:#f6f7f9;color:#1f2937;margin:0;padding:24px}}
 h1{{font-size:20px;margin:0 0 4px}} .sub{{color:#6b7280;font-size:13px;margin-bottom:18px}}
 .grid{{display:flex;flex-wrap:wrap;gap:14px;margin-bottom:18px}}
 .card{{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:14px 18px;min-width:150px;flex:1}}
 .card .k{{font-size:12px;color:#6b7280}} .card .v{{font-size:24px;font-weight:600;margin-top:4px}}
 .bar-wrap{{background:#e5e7eb;height:14px;border-radius:7px;overflow:hidden;margin:8px 0 4px}}
 .bar-wrap>div{{background:linear-gradient(90deg,#2563eb,#22c55e);height:100%}}
 .panel{{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:16px 18px;margin-bottom:18px}}
 table{{width:100%%;border-collapse:collapse;font-size:13px}}
 th,td{{text-align:left;padding:6px 8px;border-bottom:1px solid #f1f1f1}}
 td.num{{text-align:right;font-variant-numeric:tabular-nums}}
 .chart{{display:flex;align-items:flex-end;gap:10px;height:120px;margin-top:8px}}
 .bar{{flex:1;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;gap:4px;font-size:11px;color:#6b7280}}
 .barfill{{width:100%%;background:#60a5fa;border-radius:4px 4px 0 0}}
 ul{{margin:6px 0 0 18px;padding:0;font-size:13px;color:#4b5563}}
 .dot{{display:inline-block;width:9px;height:9px;border-radius:50%%;background:{live_color};margin-right:6px}}
</style></head><body>
<h1>wu2198 缺失文章补抓 · 实时进度</h1>
<div class="sub">生成于 {now} &nbsp;|&nbsp; 页面每 30 秒自动刷新 &nbsp;|&nbsp;
  <span class="dot"></span><b style="color:{live_color}">{live}</b></div>

<div class="grid">
 <div class="card"><div class="k">已完成 / 目标</div><div class="v">{done} / {total}</div>
   <div class="bar-wrap"><div style="width:{pct:.2f}%"></div></div>
   <div class="k">{pct:.2f}% ｜ 剩余 {remaining} 篇</div></div>
 <div class="card"><div class="k">当前速率（近 1 小时）</div><div class="v">{r60:.1f} <span style="font-size:13px">篇/分</span></div>
   <div class="k">10 分钟 {r10:.1f} ｜ 30 分钟 {r30:.1f}</div></div>
 <div class="card"><div class="k">预计完成</div><div class="v" style="font-size:20px">{eta_at}</div>
   <div class="k">约 {eta_h:.0f} 小时后（按当前速率）</div></div>
 <div class="card"><div class="k">今日已抓</div><div class="v">{today}</div>
   <div class="k">累计日志行 {log_lines}</div></div>
 <div class="card"><div class="k">暂存区构成</div>
   <div class="v" style="font-size:20px">{text_all} <span style="font-size:13px">有正文</span></div>
   <div class="k">纯图片 {photo_all} ｜ 空正文 {empty_all}</div></div>
</div>

<div class="panel"><b>近 12 小时产出（按落盘时间）</b><div class="chart">{bars}</div></div>

<div class="panel"><b>近 12 篇捕获</b>
<table><tr><th>落盘</th><th>文章 ID</th><th>标题</th><th>正文字数</th><th>发布时间</th><th>备注</th></tr>
{rows}</table></div>

<div class="panel"><b>永久跳过（不可恢复）</b><ul>{perm}</ul>
 <div class="k" style="font-size:12px;color:#6b7280;margin-top:6px">
 临时失败 {transient} 条（下轮自动重试）；异常样本 {warn} 条</div></div>

<div class="panel"><b>最新日志</b><div style="font-size:13px;color:#374151;font-family:Consolas,monospace">{last_line}</div></div>
</body></html>
""".format(now=time.strftime("%Y-%m-%d %H:%M:%S"), live=live, live_color=live_color,
           done=d["done"], total=d["total"], pct=pct, remaining=d["remaining"],
           r10=d["r10"], r30=d["r30"], r60=d["r60"], eta_at=d["eta_at"],
           eta_h=d["eta_min"] / 60.0, today=d["today"], log_lines=d["log_lines"],
           bars=bars, rows=rows, perm=perm,
           transient=len(d["transient"]), warn=len(d["warnings"]),
           last_line=d["last_line"],
           text_all=d["text_all"], photo_all=d["photo_all"], empty_all=d["empty_all"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", type=int, default=0, help="每 N 秒刷新一次（0 表示只刷一次）")
    args, _ = ap.parse_known_args()
    while True:
        d = collect()
        io.open(HTML, "w", encoding="utf-8").write(render(d))
        lines = []
        lines.append("=" * 62)
        lines.append("时间 %s  状态: %s" % (time.strftime("%Y-%m-%d %H:%M:%S"),
                                      "运行中" if d["log_age"] < 300
                                      else "日志 %d 分钟未更新" % int(d["log_age"] // 60)))
        lines.append("进度 %d / %d（%.2f%%）  剩余 %d 篇" % (
            d["done"], d["total"], d["done"] * 100.0 / max(d["total"], 1), d["remaining"]))
        lines.append("速率 10/30/60 分钟: %.1f / %.1f / %.1f 篇/分钟"
                     % (d["r10"], d["r30"], d["r60"]))
        lines.append("预计完成 %s（约 %.1f 小时）" % (d["eta_at"], d["eta_min"] / 60.0))
        r = d["rounds"]
        lines.append("暂存区构成: 有正文 %d | 纯图片 %d | 空正文 %d（共 %d 篇）"
                     % (d["text_all"], d["photo_all"], d["empty_all"], d["done"]))
        lines.append("已结束轮次累计: 成功 %d | 纯图片 %d | 永久跳过 %d | 临时失败 %d"
                     % (r["ok"], r["photo"], r["skip"], r["transient"]))
        lines.append("仪表盘: %s" % HTML)
        txt = "\n".join(lines)
        # 自落盘快照，供外部读取/归档，避免依赖 shell 重定向
        try:
            io.open(SNAP, "w", encoding="utf-8").write(txt + "\n")
        except Exception:
            pass
        print(txt, flush=True)
        if not args.watch:
            break
        time.sleep(args.watch)
    return 0


if __name__ == "__main__":
    sys.exit(main())