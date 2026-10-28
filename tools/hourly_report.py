# -*- coding: utf-8 -*-
"""wu2198 补抓 · 按小时进展报告（滚动刷新，供 Windows 计划任务每小时调用一次）

与旧版 tools/make_hourly_report.py 的区别（旧版的问题见下）：
  1) 停摆/异常时段由**数据自动推导**（progress.log 时间缺口 + ALERT.md 自愈记录），
     不再硬编码"16:29–18:10 停摆 95 分钟"这类历史文案 —— 旧版把一次性事故写死在
     表里，之后再停摆它依然报"一切正常"。
  2) **跨天正确**：progress.log 的行只有 HH:MM:SS、没有日期，旧版把所有日志行都
     算作"今天"，一旦跨天按小时统计就全错。本版用"时间回退即翻日"推断日期。
  3) **感知自愈系统**：读取 heal.log / heal_status.json / ALERT.md，报告里直接给出
     "巡检是否在跑、最近结论、是否发生过停滞"，并对"巡检自身也停了"做元告警。
  4) 原子写入（tmp + os.replace），避免读取时拿到半截文档；pythonw 下无控制台也能跑。

输出：docs/进展按小时.md（滚动刷新；旧版按日归档的 docs/进展按小时_20261024.md 保持不动）
      并在 output/1216826604_wayback/hourly_report.log 追加一行留痕（永不覆盖）
      异常（pythonw 下无控制台）落盘到 output/1216826604_wayback/hourly_report_error.log

用法：
    python tools/hourly_report.py
"""
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
PROG = os.path.join(OUT, "progress.log")
LISTF = os.path.join(ROOT, "temp", "wayback_cdx2.json")
SKIPF = os.path.join(OUT, "skip_permanent.json")
TRANSF = os.path.join(OUT, "failed_transient.json")
HEAL_LOG = os.path.join(OUT, "heal.log")
HEAL_STATUS = os.path.join(OUT, "heal_status.json")
ALERT_MD = os.path.join(OUT, "ALERT.md")
DOC = os.path.join(ROOT, "docs", "进展按小时.md")
LOG = os.path.join(OUT, "hourly_report.log")          # 追加式留痕（永不覆盖）
ERRLOG = os.path.join(OUT, "hourly_report_error.log")  # pythonw 下的异常落盘

GAP_ALERT_MIN = 15.0      # 日志相邻两行间隔超过此值即视为可疑空窗
GAP_MAX_HOURS = 3.0       # 超过此值的缺口按"会话间断"处理，不计入空窗
NOMINAL_RATE = 3.4        # 15 秒/篇的合规名义速率（篇/分）
STATUS_STALE_MIN = 30.0   # heal_status.json 超过此年龄 → 巡检本身可能已停

# 人工确认过的历史事实（一次性事件，不随时间变化，故保留静态记录）
HISTORIC = [
    ("16:23–16:25", "2 分", "本机 DNS 瞬时中断（连续 getaddrinfo failed，属网络类）",
     "当时触发统一的 --max-fail 8，该轮提前停止；现已改为网络类/限流类分开计数"),
    ("16:29–18:10", "95 分", "守护与抓取子进程被宿主作业对象连带强杀（无日志、无崩溃栈）",
     "静默停摆、零产出；已加【每 10 分钟自检 + 停滞即拉起】的计划任务"),
    ("14:xx 内约 12 分钟", "12 分", "双实例并发抓取（违反严格串行约定）",
     "已修正为 fail-closed 探测；自愈巡检亦遵循【宁可漏动、不可双开】"),
    ("观测口径提醒", "—", "14:xx 的 461 篇/时（7.68 篇/分）为一次性全量重试，非真实产能",
     "逐小时表已对该小时单独批注"),
    ("09:18–09:32", "14 分", "参数切换维护：抓取间隔 --gap 15 → 25，受控停机（非故障）",
     "日志断档 11 分 55 秒，未达 15 分钟空窗阈值故不入上表；切换脚本 fail-closed，"
     "核验单守护+单抓取后才放行"),
    ("09:33–10:44", "—", "降速后实测吞吐降至约 0.60 篇/分，而 429 频率未降（0.35→0.32 次/分）",
     "降速未换来成功率，反而净损产能 ⇒ 已于 10:51 回滚为 15 秒，吞吐恢复到 1.2 篇/分；"
     "留档 docs/限流降速与告警_20261025.md 第四、五节"),
    ("10:45–10:51", "6 分", "第二次参数切换维护：回滚 --gap 25 → 15，同时加载节点计数新代码",
     "受控停机，非故障；停机期间经 180 秒宽限后才终止子进程，"
     "事后核验最近 25 分钟落盘的 16 篇全部结构完整，无半截文章"),
]

# 逐小时批注：按"MM-DD HH"精确匹配，绝不外溢到其它日期（旧版只按小时匹配，
# 跨天后凌晨的"17:00"会错误继承昨天的批注）
HOUR_EXC = {
    "10-24 11": "含一次性全量重试（对已抓条目重放），非真实产能",
    "10-24 14": "含 12 分钟双实例并发（3.6 篇/分），违反串行约定",
    "10-24 15": "含双实例并发余波",
    "10-24 16": "16:29 起静默停摆，该小时后段零产出",
    "10-24 17": "整点空窗：停摆未恢复，零产出",
    "10-25 09": "含 09:18–09:32 参数切换受控停机（14 分）；09:33 起间隔 25 秒，产能随之下降",
}


def out(s):
    """pythonw 下 sys.stdout 可能为 None，静默失败即可（报告靠文件通道）。"""
    try:
        print(s)
    except Exception:
        pass


def load_json(path, default):
    try:
        with io.open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def parse_log():
    """解析 progress.log → (行列表[(时间戳, 文本)], 时间缺口列表)。

    行��只有 HH:MM:SS、**没有日期**，而日志是跨天连续追加的，首行往往不是今天
    （实测首行是昨天 07:04，全文件仅 1 处跨零点回退）。
    锚定办法：文件的 mtime 与"最后一行"同属一次写入 → 用 mtime 所在日期锚定最后
    一行，再**向前**回溯；遇到明显的时间回退（>1 小时）就减一天。
    旧实现把"今天"直接当成首行的日期、再向后翻日，会让整份日志日期偏移一天，
    逐小时的跳过/失败列全部错位到表外（表现为"跳过 0"）。
    """
    if not os.path.exists(PROG):
        return [], []
    raw = []
    with io.open(PROG, encoding="utf-8", errors="ignore") as f:
        for ln in f:
            m = re.match(r"\[(\d\d):(\d\d):(\d\d)\]\s*(.*)", ln.rstrip("\n"))
            if m:
                raw.append(((int(m.group(1)), int(m.group(2)), int(m.group(3))),
                            m.group(4)))
    if not raw:
        return [], []

    day_ts = time.mktime(time.strptime(
        time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(PROG))),
        "%Y-%m-%d"))
    ts_list = [0] * len(raw)
    prev = None
    for i in range(len(raw) - 1, -1, -1):
        h, mi, s = raw[i][0]
        if prev is not None and (h * 3600 + mi * 60 + s) > (prev[0] * 3600 + prev[1] * 60 + prev[2]) + 3600:
            day_ts -= 86400          # 向前回溯时遇到时间大幅回退 → 跨过零点
        ts_list[i] = day_ts + h * 3600 + mi * 60 + s
        prev = raw[i][0]

    rows = [(ts_list[i], raw[i][1]) for i in range(len(raw))]

    gaps = []
    for i in range(1, len(rows)):
        d = (rows[i][0] - rows[i - 1][0]) / 60.0
        if GAP_ALERT_MIN < d <= GAP_MAX_HOURS * 60:
            gaps.append((rows[i - 1][0], d, rows[i - 1][1][:60], rows[i][1][:60]))
    return rows, gaps


def current_gap():
    """读取守护脚本里当前的抓取间隔 --gap（秒）；读不到返回 None。

    为什么要读：2026-10-25 起间隔由 15 秒主动提高到 25 秒，若报告仍按"15 秒/篇 =
    3.4 篇/分"算名义速率与 ETA，就会给出偏乐观的工期，没人会去核对。
    """
    p = os.path.join(ROOT, "tools", "watchdog_run.py")
    try:
        s = io.open(p, encoding="utf-8").read()
        blk = re.search(r"CHILD_ARGS\s*=\s*\[(.*?)\]", s, re.S)
        if not blk:
            return None
        m = re.search(r'"--gap"\s*,\s*"([\d.]+)"', blk.group(1))
        return float(m.group(1)) if m else None
    except Exception:
        return None


def main():
    now = time.time()
    md = glob.glob(os.path.join(POSTS, "*.md"))
    done_ids = {os.path.basename(f)[:-3] for f in md}
    done = len(md)
    if not md:
        out("posts 目录为空，暂不生成报告")
        return 0

    # 每小时产出（以真实文件 mtime 为准，跨天安全）
    bymd = collections.Counter()
    for f in md:
        bymd[time.strftime("%m-%d %H", time.localtime(os.path.getmtime(f)))] += 1
    first_t = min(os.path.getmtime(f) for f in md)

    rows, gaps = parse_log()
    ev = collections.defaultdict(collections.Counter)
    last_line = {}
    recent_rate = [None]        # 最近一轮实测速率（篇/分），用于更贴合的 ETA
    for ts, text in rows:
        k = time.strftime("%m-%d %H", time.localtime(ts))
        t = " ".join(text.split())
        if "永久跳过" in t:
            ev[k]["skip"] += 1
        elif "纯图片/无正文" in t:
            ev[k]["image"] += 1
        elif "临时失败" in t:
            ev[k]["fail"] += 1
            if "429" in t:
                ev[k]["r429"] += 1
        elif "分类：网络类" in t:
            ev[k]["net"] += 1
        elif "分类：限流类" in t:
            ev[k]["limit"] += 1
        if "已抓" in t and "篇（经" in t:
            last_line["线"] = time.strftime("%m-%d %H:%M", time.localtime(ts)) + "  " + t
            m2 = re.search(r"已抓 (\d+) 篇（经 ([\d.]+)s，([\d.]+) 篇/分钟）", t)
            if m2:
                recent_rate[0] = float(m2.group(3))

    items = load_json(LISTF, {}).get("items", {})
    target = len(items)
    skips = load_json(SKIPF, {})
    skips = skips if isinstance(skips, dict) else {}
    trans = load_json(TRANSF, [])
    trans = trans if isinstance(trans, list) else []
    remaining = len([a for a in items if a not in done_ids and a not in skips]) if items else None
    if remaining is not None and remaining < 0:
        remaining = None          # 口径异常一律降级为"未知"，不作为结论依据

    # 自愈巡检状态
    hs = load_json(HEAL_STATUS, {})
    hs_age = None
    if os.path.exists(HEAL_STATUS):
        hs_age = (now - os.path.getmtime(HEAL_STATUS)) / 60.0
    heal_tail = []
    if os.path.exists(HEAL_LOG):
        try:
            with io.open(HEAL_LOG, encoding="utf-8", errors="ignore") as f:
                heal_tail = [l.rstrip("\n") for l in f.readlines() if "巡检结论" in l][-24:]
        except Exception:
            heal_tail = []
    alert_lines = []
    if os.path.exists(ALERT_MD):
        try:
            with io.open(ALERT_MD, encoding="utf-8", errors="ignore") as f:
                alert_lines = [l.rstrip("\n") for l in f if l.strip().startswith("|")][1:]
        except Exception:
            alert_lines = []

    # 吞吐
    wall_min = max(1.0, (now - first_t) / 60.0)
    outage_min = sum(d for _, d, _, _ in gaps)
    # ↑ 只取自动检出值：16:29–18:10 那次停摆本身就会被"日志时间缺口"检出，
    #   旧版再叠加写死的 95 分钟属重复计算，会把"扣除空窗"的速率算得虚高
    net_min = max(1.0, wall_min - outage_min)
    rate_gross = done / wall_min
    rate_net = done / net_min
    # ---- 当前参数与名义速率（跟随守护脚本，避免降速后报告失准）----
    gap = current_gap()
    nominal = (60.0 / (gap * 1.1)) if gap else NOMINAL_RATE   # 含平均 10% 抖动
    gap_txt = ("%g 秒/篇（含 0–20%% 抖动，均值约 %.1f 秒）" % (gap, gap * 1.1)) if gap \
        else "未知（沿用 15 秒/篇）"

    # ---- 限流/失败构成（自动检出：无人值守时一眼看出通道健康度）----
    last_key = time.strftime("%m-%d %H", time.localtime(now - (now % 3600) - 3600))
    prev_key = time.strftime("%m-%d %H", time.localtime(now - (now % 3600) - 7200))
    r429_last = ev[last_key].get("r429", 0)
    fail_last = ev[last_key].get("fail", 0)
    r429_prev = ev[prev_key].get("r429", 0)
    limit_pct = (100.0 * r429_last / fail_last) if fail_last else 0.0
    node_all = collections.Counter()
    node_429 = collections.Counter()
    for it in trans:
        if isinstance(it, dict):
            nd = str(it.get("node") or "未标注")
            node_all[nd] += 1
            if "429" in str(it.get("error", "")):
                node_429[nd] += 1
    alarms = []
    if r429_last >= 10:
        alarms.append("近 1 小时 429 达 **%d 次**（阈值 10）" % r429_last)
    if fail_last and limit_pct >= 30:
        alarms.append("限流占临时失败 **%.0f%%**（阈值 30%%）" % limit_pct)
    if r429_last >= 5 and r429_last > r429_prev:
        alarms.append("较上一小时**恶化**（%d → %d）" % (r429_prev, r429_last))
    elif r429_last < r429_prev and r429_prev >= 5:
        alarms.append("较上一小时缓解（%d → %d）" % (r429_prev, r429_last))

    eta_nom = (remaining / nominal / 60.0) if remaining else None
    eta_net = (remaining / rate_net / 60.0) if (remaining and rate_net > 0) else None

    L = []
    A = L.append
    A("# wu2198 补抓进展（按小时统计）")
    A("")
    A("统计时刻：%s ｜ 本报告由计划任务每小时自动刷新（`tools/hourly_report.py`）"
      % time.strftime("%Y-%m-%d %H:%M:%S"))
    A("")
    A("## 一、总览")
    A("")
    A("| 指标 | 数值 |")
    A("| --- | --- |")
    A("| 快照清单总数 | %d 篇 |" % target)
    A("| 已落盘正文 | **%d 篇**（%.1f%%）|" % (done, 100.0 * done / max(1, target)))
    A("| 永久跳过（无正文/纯图片） | %d 篇 |" % len(skips))
    A("| 临时失败待重试 | %d 篇 |" % len(trans))
    A("| 剩余待抓 | **%s** |" % ("未知（清单口径异常）" if remaining is None else "%d 篇" % remaining))
    A("| 首次落盘 | %s |" % time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(first_t)))
    A("| 已运行 | %.1f 小时 |" % (wall_min / 60.0))
    A("")
    A("## 二、按小时明细")
    A("")
    A("| 小时 | 落盘 | 永久跳过 | 纯图片 | 临时失败 | 累计落盘 | 吞吐(篇/分) | 备注 |")
    A("| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |")
    # 连续小时序列（含零产出小时）：停摆那小时必须出现在表里，否则最该看到的异常反而看不见
    cur_hour = now - (now % 3600)
    keys = []
    t = first_t - (first_t % 3600)
    while t <= cur_hour + 1:
        keys.append(time.strftime("%m-%d %H", time.localtime(t)))
        t += 3600
    cum = 0
    tot = collections.Counter()
    for k in keys:
        r = bymd.get(k, 0)
        s = ev[k].get("skip", 0)
        i = ev[k].get("image", 0)
        f = ev[k].get("fail", 0)
        cum += r
        tot["r"] += r
        tot["s"] += s
        tot["i"] += i
        tot["f"] += f
        note = HOUR_EXC.get(k, "")
        if not r and not note:
            note = "（零产出，见第三节）" if k != keys[-1] else "（当前小时，进行中）"
        A("| %s:00 | %d | %d | %d | %d | %d | %.2f | %s |"
          % (k, r, s, i, f, cum, r / 60.0, note))
    A("| **合计** | **%d** | **%d** | **%d** | **%d** | | **%.2f** | |"
      % (tot["r"], tot["s"], tot["i"], tot["f"], tot["r"] / wall_min))
    A("")
    A("> 口径提示：本表「永久跳过／纯图片／临时失败」统计的是 **progress.log 中的事件"
      "次数**（同一条目重试会重复计数，且无时间戳的续行不计入），与第一节按**条目去重**"
      "的 JSON 口径不是同一个数，仅用于看趋势与异常对比。")
    A("> ")
    A("> 另：`progress.log` 每行只有 `HH:MM:SS`、没有日期，日期由「文件末行锚定 + 向前"
      "回溯翻日」推断；本报告若发现某小时事件整体错位，请优先怀疑该推断（落盘列走文件"
      "mtime，不受影响）。")
    A("")
    A("## 三、停摆与异常时段")
    A("")
    if gaps:
        A("### 自动检出（progress.log 时间缺口 > %.0f 分钟）" % GAP_ALERT_MIN)
        A("")
        A("| 开始时点 | 空窗时长 | 缺口前最后一行 | 恢复后第一行 |")
        A("| --- | ---: | --- | --- |")
        for ts, d, before, after in gaps[-25:]:
            A("| %s | %.0f 分 | %s | %s |"
              % (time.strftime("%m-%d %H:%M:%S", time.localtime(ts)), d,
                 before.replace("|", "/"), after.replace("|", "/")))
        A("")
    else:
        A("**自动检出：无。**（progress.log 未出现 > %.0f 分钟的时间缺口）" % GAP_ALERT_MIN)
        A("")
    if alert_lines:
        A("### 自愈系统记录（ALERT.md）")
        A("")
        A("| 记录时间 | 事件 | 详情 |")
        A("| --- | --- | --- |")
        for l in alert_lines[-20:]:
            A(l)
        A("")
    else:
        A("**自愈系统记录：`ALERT.md` 不存在** → 自上线以来未发生停滞。")
        A("")
    A("### 限流与失败构成（自动检出）")
    A("")
    A("| 指标 | 近 1 小时（%s） | 上一小时（%s） |" % (last_key, prev_key))
    A("| --- | ---: | ---: |")
    A("| 临时失败事件 | %d | %d |" % (fail_last, ev[prev_key].get("fail", 0)))
    A("| 其中 HTTP 429（限流） | **%d** | %d |" % (r429_last, r429_prev))
    A("| 限流占失败比 | %.0f%% | %s |"
      % (limit_pct, ("%.0f%%" % (100.0 * ev[prev_key].get("r429", 0) / ev[prev_key]["fail"]))
         if ev[prev_key].get("fail") else "—"))
    A("")
    node_stat = load_json(os.path.join(OUT, "node_stats.json"), {})
    node_stat = node_stat if isinstance(node_stat, dict) else {}
    if node_stat:
        A("**各节点请求量与成功率**（`node_stats.json`，2026-10-25 起累计；"
          "节点按 `i % len(NODES)` **严格轮询**，理论上四家均等，故可直接横向比较）：")
        A("")
        A("| 节点 | 派发 | 成功 | 跳过 | 失败 | 其中 429 | 成功率 | 429 占派发 |")
        A("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        for nd in sorted(node_stat, key=lambda x: -node_stat[x].get("try", 0)):
            v = node_stat[nd]
            if not isinstance(v, dict):
                continue
            t = max(1, v.get("try", 0))
            okn = v.get("ok", 0) + v.get("photo", 0)
            A("| %s | %d | %d | %d | %d | %d | %.0f%% | %.0f%% |"
              % (nd, v.get("try", 0), okn, v.get("skip", 0), v.get("fail", 0),
                 v.get("err429", 0), 100.0 * okn / t, 100.0 * v.get("err429", 0) / t))
        A("")
        A("> 「派发」是分母——这正是此前缺的那一项：没有它，「429 全集中在某节点」"
          "与「该节点只是流量更大」无法区分。")
        A(">")
        A("> ⚠ 读表提醒：**allallow 的「其中 429」列恒为 0，是假象而不是好消息。**"
          "该反代在自身被限流时仍回 HTTP 200，正文却是 429 错误页；抓取器不报错，"
          "解析器因取不到标题与正文判为 no_body，于是被记成「永久跳过」而非「限流失败」——"
          "**它的 429 藏在「跳过」列里**。nocors 反而诚实：直接抛 HTTP 429 → 记入失败列 → 日后重试。"
          "详见 docs/限流降速与告警_20261025.md 第六节。")
        A("")
    if node_all:
        A("**历史失败条目命中的代理节点**（`failed_transient.json`，按条目去重，"
          "为计数上线前积累的数据，无请求量分母，仅供参考）：")
        A("")
        A("| 节点 | 失败条目 | 其中 429 |")
        A("| --- | ---: | ---: |")
        for nd, cnt in node_all.most_common(6):
            A("| %s | %d | %d |" % (nd, cnt, node_429.get(nd, 0)))
        A("")
    if alarms:
        A("**判定：⚠ 需关注** —— " + "；".join(alarms) + "。")
    else:
        A("**判定：正常**（近 1 小时 429 %d 次、占失败 %.0f%%，未触发阈值）。"
          % (r429_last, limit_pct))
    A("")
    A("> 阈值与口径：「429 ≥ 10 次/时」「限流占失败 ≥ 30%」「较上一小时恶化」任一命中即标 ⚠。"
      "事件次数来自 `progress.log`（同一条目重试会重复计数），节点分布来自 "
      "`failed_transient.json`（按条目去重）。")
    A("")
    A("### 人工确认过的历史事件（静态记录，非本报告推导）")
    A("")
    A("| 时段 | 时长 | 事件 | 处置 |")
    A("| --- | --- | --- | --- |")
    for t0, dur, what, how in HISTORIC:
        A("| %s | %s | %s | %s |" % (t0, dur, what, how))
    A("")
    A("## 四、吞吐与预计完成")
    A("")
    A("| 口径 | 数值 | 说明 |")
    A("| --- | --- | --- |")
    A("| 名义速率 | %.1f 篇/分 | 当前间隔 %s，严格串行，合规访问 |" % (nominal, gap_txt))
    A("| 实际速率（含空窗） | %.2f 篇/分 | 被停摆/空窗拉低 |" % rate_gross)
    A("| 实际速率（扣除空窗） | %.2f 篇/分 | 反映真实产能 |" % rate_net)
    if recent_rate[0]:
        eta_recent = remaining / recent_rate[0] / 60.0 if remaining else None
        A("| **最近一轮实测** | **%.2f 篇/分** | 取 progress.log 中最新一条吞吐线，"
          "**最贴近当前真实产能** |" % recent_rate[0])
        if eta_recent:
            A("| 剩余 %d 篇·最近实测 | 约 %.0f 小时（%.1f 天）| 推荐按此安排后续会话 |"
              % (remaining, eta_recent, eta_recent / 24))
    if eta_nom:
        A("| 剩余 %d 篇·名义速率 | 约 %.0f 小时（%.1f 天）| 按当前间隔 %s 推算 |"
          % (remaining, eta_nom, eta_nom / 24, gap_txt))
        A("| 剩余 %d 篇·实际速率 | 约 %.0f 小时（%.1f 天）| 含失败重试损耗 |"
          % (remaining, eta_net, eta_net / 24))
    else:
        A("| 剩余与预计 | 未知 | 清单口径异常，已降级，不给出误导性估算 |")
    A("")
    A("## 五、当前运行状态")
    A("")
    A("| 项目 | 状态 |")
    A("| --- | --- |")
    if hs:
        A("| 抓取进程数 | %s |" % hs.get("crawlers", "未知"))
        A("| 守护进程数 | %s |" % hs.get("guards", "未知"))
        A("| 进度日志年龄 | %s 秒 |" % hs.get("log_age_sec", "未知"))
        A("| 最近一次自愈巡检 | %s → %s |" % (hs.get("time", "未知"), hs.get("action", "未知")))
    else:
        A("| 自愈巡检状态 | 尚未生成 heal_status.json |")
    if hs_age is None:
        A("| 巡检元告警 | ⚠ 未找到 heal_status.json，巡检可能从未运行 |")
    elif hs_age > STATUS_STALE_MIN:
        A("| 巡检元告警 | ⚠ heal_status.json 已 %.0f 分钟未更新"
          "（阈值 %.0f）→ **巡检任务本身可能已停** |" % (hs_age, STATUS_STALE_MIN))
    else:
        A("| 巡检元告警 | 无（%.0f 分钟前更新，巡检在正常运行）|" % hs_age)
    if last_line.get("线"):
        A("| 最新吞吐线 | %s |" % last_line["线"])
    A("")
    if heal_tail:
        A("## 六、自愈巡检最近记录")
        A("")
        A("```")
        for l in heal_tail[-12:]:
            A(l)
        A("```")
        A("")

    txt = "\n".join(L)
    tmp = DOC + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write(txt)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, DOC)

    # 追加式留痕：文档是被整体重写的，一旦哪天写入异常/被误覆盖，逐小时证据就没了。
    # 这一行只追加、永不覆盖，事后可据此还原"报告是什么时候停的、停在什么数字上"。
    # 注：任务在整点触发，此刻"当前小时"必然刚起步，报它只会恒为 0 篇（看着像停摆），
    #     故这里报**上一个完整小时**的产出。
    hour_key = time.strftime("%m-%d %H", time.localtime(cur_hour - 3600))
    try:
        with io.open(LOG, "a", encoding="utf-8") as f:
            f.write("[%s] 累计 %d 篇 | 上一小时 %d 篇 | 清单 %d | 跳过 %d / 纯图片 %d / 临时失败 %d"
                    " | 剩余 %s | 空窗 %d 段/%.0f 分 | 实测速率 %s 篇/分 | 巡检 %s | ALERT %s\n"
                    % (time.strftime("%Y-%m-%d %H:%M:%S"), done, bymd.get(hour_key, 0), target,
                       tot["s"], tot["i"], tot["f"],
                       "未知" if remaining is None else str(remaining), len(gaps), outage_min,
                       ("%.2f" % recent_rate[0]) if recent_rate[0] else "未知",
                       hs.get("action", "未知") if hs else "无状态",
                       "有 %d 条" % len(alert_lines) if alert_lines else "无"))
    except Exception as e:
        out("追写留痕日志失败：%s" % e)

    out(txt)
    out("\n已写入 %s（%d 行，%d 字符）" % (DOC, len(L), len(txt)))
    return 0


if __name__ == "__main__":
    # pythonw 无控制台，异常若直接抛出会**静默消失**——正是"报告悄悄停更"的经典成因。
    # 这里兜住并落盘，巡检/人查一眼就知道是脚本崩了，而不是没数据。
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        import traceback
        try:
            with io.open(ERRLOG, "a", encoding="utf-8") as f:
                f.write("\n[%s] hourly_report 异常：\n%s\n"
                        % (time.strftime("%Y-%m-%d %H:%M:%S"), traceback.format_exc()))
        except Exception:
            pass
        sys.exit(1)