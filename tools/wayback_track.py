# -*- coding: utf-8 -*-
"""wu2198 Wayback 补抓进度跟踪器（只读，不出网）。

设计依据：**进度不能看进程是否存活，要看数据是否在长**。
posts/*.md 的 mtime 就是落盘时刻，是唯一可信的产出证据，
因此产出速率一律由 mtime 反推，不采信进程自报。

每次运行做四件事：
  1. 采集当前进度（已落盘 / 清单 / 永久跳过 / 真实剩余）
  2. 与 track_history.jsonl 的上一条快照相减，给出增量与速率
  3. 判定是否停滞（距最后一次落盘超过阈值），并统计近期失败类型
  4. 停滞状态**翻转**时往 ALERT.md 写一条（停滞一条、恢复一条），平稳期不重复写

用法：
  python tools/wayback_track.py                      # 打印一次
  python tools/wayback_track.py --quiet              # 只记录，不打印（供计划任务）
  python tools/wayback_track.py --append docs/x.md   # 追加写入 Markdown 报告
"""
import argparse
import collections
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
PROGRESS = os.path.join(OUT, "progress.log")
LISTF = os.path.join(ROOT, "temp", "wayback_cdx2.json")
SKIPF = os.path.join(OUT, "skip_permanent.json")
HIST = os.path.join(OUT, "track_history.jsonl")

STALL_MIN = 90          # 超过该分钟数没有新落盘，判定为停滞并告警
FAIL_WINDOW = 60        # 失败统计回看窗口（分钟）
ALERT = os.path.join(OUT, "ALERT.md")


def load_counts():
    """清单总数 / 永久跳过数。缺失时返回 0，不阻断主流程。"""
    total = 0
    try:
        d = json.load(io.open(LISTF, encoding="utf-8"))
        items = d.get("items", d)
        total = len(items)
    except Exception:
        pass
    skip = 0
    try:
        skip = len(json.load(io.open(SKIPF, encoding="utf-8")))
    except Exception:
        pass
    return total, skip


def posts_stat():
    """返回 (篇数, 最近落盘时间戳)。mtime 即落盘时刻。"""
    files = glob.glob(os.path.join(POSTS, "*.md"))
    mtimes = []
    for f in files:
        try:
            mtimes.append(os.path.getmtime(f))
        except OSError:
            pass
    return len(files), (max(mtimes) if mtimes else 0), mtimes


def produced(mtimes, minutes):
    """最近 N 分钟内落盘的篇数。"""
    edge = time.time() - minutes * 60
    return sum(1 for m in mtimes if m >= edge)


def hourly(mtimes, hours=24):
    """最近 N 小时逐小时落盘数，索引 0 = 当前小时。"""
    now = time.time()
    c = collections.Counter()
    for m in mtimes:
        h = int((now - m) // 3600)
        if 0 <= h < hours:
            c[h] += 1
    return c


def recent_failures(minutes=FAIL_WINDOW):
    """解析 progress.log 尾部，统计近期失败。

    progress.log 的行首只有 HH:MM:SS、没有日期，故从最后一行往前扫，
    一旦时间倒退即判定跨天，用偏移量换算成“多久以前”。
    """
    try:
        lines = io.open(PROGRESS, encoding="utf-8", errors="ignore").readlines()[-4000:]
    except Exception:
        return collections.Counter(), collections.Counter()

    now = time.localtime()
    now_min = now.tm_hour * 60 + now.tm_min
    stamps = []
    for ln in lines:
        m = re.match(r"\[(\d\d):(\d\d):(\d\d)\]", ln)
        stamps.append((int(m.group(1)) * 60 + int(m.group(2)) if m else None, ln))

    bychan = collections.Counter()
    bykind = collections.Counter()
    offset = 0                      # 0 = 今天，1 = 昨天
    prev = None
    pending = None                  # 上一行的通道，用于配对“分类：”行
    for tmin, ln in reversed(stamps):
        if tmin is None:
            continue
        if prev is not None and tmin > prev:
            offset += 1
        prev = tmin
        age = offset * 1440 + (now_min - tmin)
        if age < 0 or age > minutes:
            continue
        m = re.search(r"临时失败\((\w+)\)", ln)
        if m:
            bychan[m.group(1)] += 1
            pending = True
            continue
        if "分类：" in ln and pending:
            if "限流类" in ln:
                bykind["限流(429/5xx)"] += 1
            elif "网络类" in ln:
                bykind["网络(超时/重置)"] += 1
            else:
                bykind["其他"] += 1
            pending = None
    return bychan, bykind


def read_hist():
    """读最后一条历史快照，用于算增量。"""
    try:
        lines = io.open(HIST, encoding="utf-8").readlines()
        return json.loads(lines[-1]) if lines else None
    except Exception:
        return None


def append_hist(rec):
    with io.open(HIST, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def fmt_eta(hours):
    if hours <= 0 or hours > 24 * 60:
        return "—"
    t = time.localtime(time.time() + hours * 3600)
    return "%s（%.1f 小时后）" % (time.strftime("%m-%d %H:%M", t), hours)


def maybe_alert(rec, prev, last_mtime, bychan, alert_path=None):
    """只在状态翻转时往 ALERT.md 追加一行。

    正常 → 停滞：记一条（含最后落盘时刻、已停时长、近期失败分布）
    停滞 → 恢复：补一条，形成有始有终的台账
    停滞期间每 30 分钟跑一次不会重复刷屏，状态机取自 track_history 的上一条快照，
    因此进程重启、手动重跑都不会造成重复或漏报。
    """
    path = alert_path or ALERT
    was = bool(prev and prev.get("stalled"))
    now = bool(rec["stalled"])
    if now == was:
        return None

    if now:
        since = (time.strftime("%Y-%m-%d %H:%M", time.localtime(last_mtime))
                 if last_mtime else "未知")
        detail = ("最后一次落盘 %s，已 %.0f 分钟无新增（累计 %d 篇）"
                  % (since, rec["idle_min"], rec["posts"]))
        if bychan:
            detail += "；近 %d 分钟失败 %s" % (
                FAIL_WINDOW, "、".join("%s %d 次" % kv for kv in bychan.most_common()))
        else:
            detail += "；近 %d 分钟无失败记录，疑为写盘或调度问题" % FAIL_WINDOW
        event = "产出停滞"
    else:
        start = prev["ts"] - prev.get("idle_min", 0) * 60
        detail = ("停滞约 %.0f 分钟，累计 %d 篇，已自动续抓"
                  % ((rec["ts"] - start) / 60.0, rec["posts"]))
        event = "停滞已恢复"

    try:
        need_head = not os.path.exists(path) or os.path.getsize(path) == 0
        with io.open(path, "a", encoding="utf-8") as f:
            if need_head:
                f.write("# 抓取停滞告警\n\n| 记录时间 | 事件 | 详情 |\n| --- | --- | --- |\n")
            else:
                with io.open(path, encoding="utf-8") as g:
                    if not g.read()[-2:].endswith("\n"):
                        f.write("\n")
            f.write("| %s | %s | %s |\n" % (rec["time"], event, detail))
    except Exception:
        return None            # 告警失败绝不能拖垮跟踪任务本身
    return "【告警】已写入 ALERT.md：%s — %s" % (event, detail)


def build():
    total, skip = load_counts()
    n, last, mtimes = posts_stat()
    remain = max(total - n - skip, 0)

    p30, p60, p180, p1440 = (produced(mtimes, x) for x in (30, 60, 180, 1440))
    rate3h = p180 / 3.0               # 篇/小时
    eta_h = remain / rate3h if rate3h > 0 else 0

    idle_min = (time.time() - last) / 60.0 if last else 10 ** 6
    stalled = idle_min > STALL_MIN

    bychan, bykind = recent_failures()
    prev = read_hist()
    delta = (n - prev["posts"]) if prev else None
    since = ""
    if prev:
        gap = (time.time() - prev["ts"]) / 60.0
        since = "距上次快照 %s 分钟，+%d 篇" % ("%.0f" % gap, n - prev["posts"])

    # 记录本段停滞的起点，供恢复时计算总停滞时长
    stall_since = None
    if stalled:
        stall_since = (prev.get("stall_since") if prev and prev.get("stalled") else None) \
            or time.strftime("%Y-%m-%d %H:%M", time.localtime(last or time.time()))

    rec = {"ts": time.time(), "time": time.strftime("%Y-%m-%d %H:%M:%S"),
           "posts": n, "total": total, "skip": skip, "remaining": remain,
           "stalled": stalled, "idle_min": round(idle_min, 1),
           "stall_since": stall_since}
    append_hist(rec)
    alert_line = maybe_alert(rec, prev, last, bychan)

    out = []
    add = out.append
    add("=" * 74)
    add("wu2198 Wayback 补抓进度   %s" % rec["time"])
    add("=" * 74)
    add("清单总数    : %d 篇" % total)
    add("已落盘      : %d 篇（%.2f%%）" % (n, 100.0 * n / max(total, 1)))
    add("永久跳过    : %d 篇" % skip)
    add("真实剩余    : %d 篇  ← 清单 - 已落盘 - 永久跳过" % remain)
    if delta is not None:
        add("增量        : %s" % since)
    add("")
    add("产出  近30分 %d 篇 ｜ 近1时 %d 篇 ｜ 近3时 %d 篇（%.1f 篇/时）｜ 近24时 %d 篇"
        % (p30, p60, p180, rate3h, p1440))
    add("预计完成    : %s" % fmt_eta(eta_h))
    add("")
    if stalled:
        add("【停滞】距最后一次落盘已 %.0f 分钟（阈值 %d），当前无产出。" % (idle_min, STALL_MIN))
    else:
        add("状态        : 正常运行（最后一次落盘 %.1f 分钟前）" % idle_min)
    if bychan:
        add("近%d分钟失败: %s" % (FAIL_WINDOW, dict(bychan)))
    if bykind:
        add("失败性质    : %s" % dict(bykind))
    if alert_line:
        add("")
        add(alert_line)

    h = hourly(mtimes, 24)
    add("")
    add("最近 24 小时逐小时落盘（左＝当前小时，向右渐远）：")
    seq = [h.get(i, 0) for i in range(24)]
    for i in range(0, 24, 8):
        part = seq[i:i + 8]
        add("   -%d~-%dh   : %s" % (i, i + len(part) - 1,
                                    " ".join("%5d" % x for x in part)))
    add("=" * 74)

    # 供 --append 使用的单行摘要：每半小时一条，避免日志膨胀
    brief = "- %s ｜ %d 篇（%s）｜剩余 %d ｜%.1f 篇/时 ｜%s" % (
        rec["time"][5:16], n,
        ("+%d" % delta) if delta is not None else "基线",
        remain, rate3h,
        ("停滞 %.0f 分钟" % idle_min) if stalled else "正常")
    return "\n".join(out), rec, brief


def main():
    # pythonw 下没有 stdout，裸 print 会抛 AttributeError 并吞掉整个任务。
    if sys.stdout is None:
        sys.stdout = io.open(os.devnull, "w", encoding="utf-8")

    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true", help="只记录快照，不打印")
    ap.add_argument("--append", help="把单行摘要追加写入指定 Markdown 文件")
    args = ap.parse_args()

    text, _, brief = build()
    if args.append:
        p = args.append if os.path.isabs(args.append) else os.path.join(ROOT, args.append)
        new = not os.path.exists(p)
        with io.open(p, "a", encoding="utf-8") as f:
            if new:
                f.write("# wu2198 补抓跟踪流水\n\n"
                        "由 `tools/wayback_track.py` 自动追加，每 30 分钟一条。\n\n")
            f.write(brief + "\n")
    if not args.quiet:
        print(text)


if __name__ == "__main__":
    main()
