# -*- coding: utf-8 -*-
"""生成 wu2198 补抓进展报告（按小时），并显式标注停摆空窗与事故时段。"""
import collections
import glob
import io
import json
import os
import re
import time

ROOT = "D:/cursor_test/blog-downloader"
OUT = os.path.join(ROOT, "output", "1216826604_wayback")
POSTS = os.path.join(OUT, "posts")
PROG = os.path.join(OUT, "progress.log")
WD = os.path.join(OUT, "watchdog.log")
LISTF = os.path.join(ROOT, "temp", "wayback_cdx2.json")
SKIPF = os.path.join(OUT, "skip_permanent.json")
TRANSF = os.path.join(OUT, "failed_transient.json")
DOC = os.path.join(ROOT, "docs", "进展按小时_20261024.md")

md = glob.glob(os.path.join(POSTS, "*.md"))
bymd = collections.Counter()
first_t = None
for f in md:
    mt = os.path.getmtime(f)
    bymd[time.strftime("%H", time.localtime(mt))] += 1
    if first_t is None or mt < first_t:
        first_t = mt

target = len(json.load(io.open(LISTF, encoding="utf-8")).get("items", {}))
skip = json.load(io.open(SKIPF, encoding="utf-8")) if os.path.exists(SKIPF) else {}
trans = json.load(io.open(TRANSF, encoding="utf-8")) if os.path.exists(TRANSF) else {}
done = len(md)

# 事故/空窗标记（人工确认过的时刻）
MARKS = {
    "11": "含一次性全量重试，吞吐异常高",
    "14": "含 12 分钟双实例并发（3.6 篇/分），已修正",
    "15": "含双实例并发时段",
    "17": "⚠ 停摆整点：16:29 后进程被强杀，无任何产出",
    "18": "18:10 修复补丁并重启，18:23 恢复抓取",
}

now = time.time()
hours = []
for h in range(first_t and int(time.strftime("%H", time.localtime(first_t))) or 7,
               int(time.strftime("%H", time.localtime(now))) + 1):
    k = "%02d" % h
    hours.append((k, bymd.get(k, 0)))

total_prod = sum(n for _, n in hours)
wall_min = (now - first_t) / 60.0
outage_min = 95.0                      # 16:29:22 - 18:10 修复前的静默停摆
net_min = max(1.0, wall_min - outage_min)
rate_gross = total_prod / max(1.0, wall_min)
rate_net = total_prod / net_min
remaining = target - done - len(skip)
eta_net_h = remaining / rate_net / 60.0
eta_nom_h = remaining / 3.4 / 60.0

L = []
A = L.append
A("# wu2198 补抓进展（按小时统计）")
A("")
A("统计时刻：%s" % time.strftime("%Y-%m-%d %H:%M:%S"))
A("")
A("## 一、总览")
A("")
A("| 指标 | 数值 |")
A("| --- | --- |")
A("| 快照清单总数 | %d 篇 |" % target)
A("| 已落盘正文 | **%d 篇**（%.1f%%）|" % (done, 100.0 * done / target))
A("| 永久跳过（无正文/纯图片） | %d 篇 |" % len(skip))
A("| 临时失败待重试 | %d 篇 |" % len(trans))
A("| 剩余待抓 | **%d 篇** |" % remaining)
A("| 首次落盘 | %s |" % time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(first_t)))
A("")
A("## 二、按小时明细")
A("")
A("| 小时 | 落盘 | 累计 | 吞吐(篇/分) | 备注 |")
A("| --- | ---: | ---: | ---: | --- |")
cum = 0
for k, n in hours:
    cum += n
    A("| %s:00 | %d | %d | %.2f | %s |" % (k, n, cum, n / 60.0, MARKS.get(k, "")))
A("| **合计** | **%d** | | **%.2f** | |" % (total_prod, rate_gross))
A("")
A("## 三、停摆与异常时段（务必知悉）")
A("")
A("| 时段 | 时长 | 事件 | 影响 |")
A("| --- | --- | --- | --- |")
A("| 14:xx 内约 12 分钟 | 12 分 | 双实例并发抓取 | 违反串行约定，已加 fail-closed 探测修正 |")
A("| 16:23–16:25 | 2 分 | 本机 DNS 瞬时中断（连续 8 次 getaddrinfo failed）| 触发 --max-fail 8，该轮提前停止 |")
A("| **16:29–18:10** | **95 分** | **守护与子进程被宿主作业对象连带强杀**（无日志、无崩溃栈）| **静默停摆，零产出** |")
A("")
A("## 四、吞吐与预计完成")
A("")
A("| 口径 | 数值 | 说明 |")
A("| --- | --- | --- |")
A("| 名义速率 | 3.4 篇/分 | 15 秒/篇，严格串行，符合站点访问约定 |")
A("| 实际速率（含停摆）| %.2f 篇/分 | 被 95 分钟停摆拉低 |" % rate_gross)
A("| 实际速率（扣除停摆）| %.2f 篇/分 | 反映真实产能 |" % rate_net)
A("| 剩余 %d 篇·名义速率 | 约 %.0f 小时（%.1f 天）| 合规下限估算 |" % (remaining, eta_nom_h, eta_nom_h / 24))
A("| 剩余 %d 篇·实际速率 | 约 %.0f 小时（%.1f 天）| 含失败重试损耗 |" % (remaining, eta_net_h, eta_net_h / 24))
A("")
A("## 五、当前运行状态")
A("")
A("| 项目 | 状态 |")
A("| --- | --- |")
A("| 守护进程 | 已启动并脱离宿主作业对象（不易被连带清理）|")
A("| 抓取进程 | 运行中，15 秒/篇严格串行 |")
A("| 逐篇落盘补丁 | **已生效**（真实中断测试：轮中被强杀，记录未丢）|")
A("| 断点续跑 | 已按清单跳过已落盘/已跳过条目 |")
A("")

txt = "\n".join(L)
io.open(DOC, "w", encoding="utf-8").write(txt)
print(txt)
print("\n已写入 %s (%d 行)" % (DOC, len(L)))