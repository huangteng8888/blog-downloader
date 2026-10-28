# -*- coding: utf-8 -*-
"""抓取守护脚本：检测抓取进程退出即自动续跑（等待接管语义）。

用法：
    python tools/watchdog_run.py --dry-run     # 体检：环境/单实例/队列/存量，不启动任何进程
    python tools/watchdog_run.py --detach      # 后台启动守护（推荐）
    python tools/watchdog_run.py --status      # 查看状态
    python tools/watchdog_run.py --stop        # 请求退出（写 STOP 旗标，等当前篇抓完）
    python tools/watchdog_run.py               # 前台守护（关窗口即停）

核心安全设计（重要）：
  1) 单实例：已有守护存活 → 拒绝重复启动
  2) **等待接管**：若已有抓取进程（无论谁启动的）在跑，守护只等待、不启动，
     因此可以在抓取进行中安全启动本脚本，不会变成双实例并发
  3) **每轮启动前二次校验**：确认当前无任何 wayback_resume 进程，才拉起新的，
     即便有人手动再起一个抓取进程，也不会被叠加
  4) 子进程参数与人工启动一致（**2026-10-25：`--gap 15`**）。当日曾短暂提到 25，
     经实测判定为净损后已回滚；参数的来龙去脉见文件末尾 `CHILD_ARGS` 处的注释，
     不提高频率、不加并发、不改动任何既有数据
  5) 连续 N 轮零增量 → 判定无可抓内容，自行退出；队列剩余为 0 → 自行退出
  6) 出现 STOP 旗标 → 等子进程抓完当前篇后退出

产出（只新增这两个文件，不动其他数据）：
  output/1216826604_wayback/watchdog.log   # 守护日志
  output/1216826604_wayback/watchdog.pid   # 守护 PID
"""
import argparse
import io
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "output", "1216826604_wayback")
POSTS_DIR = os.path.join(OUT_DIR, "posts")
SKIP_FILE = os.path.join(OUT_DIR, "skip_permanent.json")
LIST_FILE = os.path.join(ROOT, "temp", "wayback_cdx2.json")

LOG_FILE = os.path.join(OUT_DIR, "watchdog.log")
PID_FILE = os.path.join(OUT_DIR, "watchdog.pid")
STOP_FLAG = os.path.join(OUT_DIR, "STOP_WATCHDOG.flag")
PROG_LOG = os.path.join(OUT_DIR, "progress.log")   # 抓取进度日志，用于存活性的交叉验证

CHILD = os.path.join("tools", "wayback_resume.py")
# 2026-10-25 09:20 修订：--gap 15 → 25（人工决策"主动降速换成功率"）。
# 2026-10-25 10:45 撤销：--gap 25 → 15（**实测该决策为净损，已回滚**）。
#
# 降速依据：昨夜 02:00 起 429 占临时失败 64%，实测吞吐由约 1.5 篇/分降到 1.50 篇/分。
# 撤销依据（以落盘文件 mtime 为地面真值，观察 34 分钟）：
#   降速后吞吐 ≈0.60 篇/分（每 10 分钟仅落 4–7 篇），而 429 频率几乎未变
#   （0.35 → 0.32 次/分）。请求量减半而限流强度不变 ⇒ 限流不随访问节奏变化，
#   属于节点级配额/封锁，降速只会净损产能。详见 docs/限流降速与告警_20261025.md。
#
# 结论留档：**遇到 429 不要靠"慢下来"解决**，应先补各节点请求量/成功率计数，再定节点策略。
CHILD_ARGS = ["--seconds", "28800", "--gap", "15", "--max-fail", "8"]
CHILD_KEY = "wayback_resume"        # 用于识别抓取进程的命令行关键字

PY = sys.executable


def log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with io.open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_json(path, default):
    try:
        with io.open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def count_posts():
    try:
        return len([f for f in os.listdir(POSTS_DIR) if f.endswith(".md")])
    except Exception:
        return 0


def remaining():
    """队列中既没落盘、也不在永久失败清单里的条数。"""
    data = load_json(LIST_FILE, {}).get("items", {})
    if not data:
        return None
    done = set()
    if os.path.isdir(POSTS_DIR):
        done = {f[:-3] for f in os.listdir(POSTS_DIR) if f.endswith(".md")}
    skip = set(load_json(SKIP_FILE, {}).keys())
    return len([a for a in data if a not in done and a not in skip])


def proc_alive(pid):
    try:
        out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/NH"],
                             capture_output=True, text=True, timeout=30).stdout or ""
        return str(pid) in out
    except Exception:
        return False


def _ps_python_cmds():
    """枚举所有 python.exe / pythonw.exe 的 (pid, 命令行)；返回 None 表示**本次查询不可信**。

    事故教训（2026-10-24）：旧实现查询失败时 `return []`，被上层读成"当前没有
    抓取进程"，于是守护又拉起一个实例 —— 15:40-15:52 出现双实例并发抓取，
    破坏了"严格串行、15 秒/篇"的约定。**查询失败必须表示"不知道"，绝不能表示"没有"。**

    事故教训（2026-10-25）：过滤器只写了 `name='python.exe'`，而计划任务是用
    **pythonw.exe** 拉起自愈、自愈又用 sys.executable(=pythonw.exe) 拉起守护，
    于是 pythonw 守护对这套枚举**完全不可见**，直接后果有二：
      ① `probe()` 的自检"枚举里必须能看到自身 PID"对 pythonw 守护永远失败 → 一律
         unknown → 这些守护永远不敢拉起抓取进程（自愈链实际是断的）；
      ② `_pid_is_our_watchdog()` 拿 pythonw 守护的 PID 去 python.exe 列表里找，
         必然找不到 → 判定"PID 文件过期"→ 删掉 → 再拉起一个，循环累积到 4 个重复守护。
    因此过滤条件必须同时覆盖 python.exe 与 pythonw.exe。
    """
    cmd = ("Get-CimInstance Win32_Process -Filter \"name='python.exe' or name='pythonw.exe'\" | "
           "ForEach-Object { $_.ProcessId.ToString() + '|' + $_.CommandLine }")
    try:
        p = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        if p.returncode != 0:
            return None
        raw = p.stdout or b""
        txt = raw.decode("utf-8", "ignore")
        if "|" not in txt:                       # 少数环境 PowerShell 管道输出为 UTF-16
            alt = raw.decode("utf-16", "ignore")
            if "|" in alt:
                txt = alt
        rows = []
        for ln in txt.splitlines():
            ln = ln.strip()
            if "|" in ln:
                spid, c = ln.split("|", 1)
                if spid.strip().isdigit():
                    rows.append((int(spid.strip()), c.strip()))
        return rows or None
    except Exception:
        return None


def probe():
    """探测外部抓取进程。返回 (state, detail)，state ∈ {'yes','no','unknown'}。

    自检：枚举结果里必须能看到本进程自身，否则说明枚举不完整 → 归为 'unknown'。
    无法确认时一律按"有"处理（fail-closed），宁可多等一轮，不可重复启动。
    """
    me = os.getpid()
    for _ in range(3):
        rows = _ps_python_cmds()
        if rows is not None:
            if me not in set(p for p, _ in rows):
                return ("unknown", "枚举自检失败：未查到自身 PID %d，结果不可信" % me)
            hits = [c for p, c in rows if CHILD_KEY in c and p != me]
            return (("yes", hits) if hits else ("no", []))
        time.sleep(3)
    return ("unknown", "进程查询连续 3 次失败（PowerShell/WMI 不可用）")


def log_age():
    """进度日志最近一次写入距今秒数；取不到返回 None。"""
    try:
        return time.time() - os.path.getmtime(PROG_LOG)
    except Exception:
        return None


def read_pid():
    try:
        with io.open(PID_FILE, encoding="utf-8") as f:
            return int(f.read().strip())
    except Exception:
        return None


def _pid_is_our_watchdog(pid):
    """判断某 PID 是否**确实是本守护**（返回 True/False；None 表示无法确认）。

    缺陷修复（2026-10-24 18:1x）：旧逻辑用 proc_alive(pid) 只看"该 PID 是否存在"，
    而 Windows 会复用 PID。守护被强杀后 watchdog.pid 残留 19168，该号此后被一个
    无关进程占用 → 守护误判"自己还在运行"，**拒绝重启**，抓取无法恢复。
    必须按命令行身份核对，不能只看 PID 存在性。
    """
    rows = _ps_python_cmds()
    if rows is None:
        return None
    for p, c in rows:
        if p == pid:
            return ("watchdog_run.py" in c)
    return False


def _other_watchdogs():
    """枚举**除自身以外**的守护进程 (pid, cmd)；返回 None 表示无法确认。

    2026-10-25 新增。此前单实例判定只看 watchdog.pid 这一个文件，一旦该文件被
    误判为"过期"（见 _ps_python_cmds 的第二条教训），就能无限累积重复守护。
    改为直接枚举"还有谁在跑 watchdog_run.py"——这是身份判断，不依赖任何文件状态。
    """
    rows = _ps_python_cmds()
    if rows is None:
        return None
    me = os.getpid()
    return [(p, c) for p, c in rows if "watchdog_run.py" in c and p != me]


def do_status():
    pid = read_pid()
    alive = bool(pid and _pid_is_our_watchdog(pid) is True)
    print("守护进程     : %s" % ("运行中 (PID %d)" % pid if alive else "未运行"))
    print("STOP 旗标    : %s" % ("存在（守护将退出）" if os.path.exists(STOP_FLAG) else "无"))
    print("已落盘篇数   : %d" % count_posts())
    print("队列剩余     : %s" % remaining())
    state, detail = probe()
    print("抓取进程     : %s" % {"yes": "运行中（%d 个）" % len(detail),
                                 "no": "无",
                                 "unknown": "无法确认（%s）" % detail}[state])
    if state == "yes":
        for r in detail:
            print("               %s" % r[:110])
    age = log_age()
    print("进度日志     : %s" % ("未找到" if age is None else "%.0f 秒前写入" % age))
    tail = ""
    if os.path.exists(LOG_FILE):
        try:
            with io.open(LOG_FILE, encoding="utf-8", errors="ignore") as f:
                tail = "".join(f.readlines()[-3:])
        except Exception:
            pass
    if tail:
        print("--- 守护日志尾部 ---")
        print(tail.rstrip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只体检，不启动")
    ap.add_argument("--detach", action="store_true", help="后台运行")
    ap.add_argument("--stop", action="store_true", help="写 STOP 旗标请求退出")
    ap.add_argument("--status", action="store_true", help="只看状态")
    ap.add_argument("--pause", type=float, default=60.0, help="两轮之间等待秒数（默认 60）")
    ap.add_argument("--poll", type=float, default=30.0, help="等待外部抓取进程的轮询间隔秒数（默认 30）")
    ap.add_argument("--max-idle-rounds", type=int, default=3, help="连续零增量多少轮后自行退出（默认 3）")
    ap.add_argument("--stop-grace", type=float, default=180.0, help="收到 STOP 后等子进程收尾的秒数")
    ap.add_argument("--max-rounds", type=int, default=0, help="最多拉起多少轮（0=不限，调试用）")
    args = ap.parse_args()

    if args.status:
        do_status()
        return 0

    if args.stop:
        with io.open(STOP_FLAG, "w", encoding="utf-8") as f:
            f.write("stop requested at %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
        print("已写入 STOP 旗标：%s" % STOP_FLAG)
        print("守护将在当前篇抓完后退出（最长等待 %d 秒）。删除该文件可取消。" % int(args.stop_grace))
        return 0

    pid = read_pid()
    ident = _pid_is_our_watchdog(pid) if pid else False
    if ident is None:
        print("无法确认 PID %s 的身份（进程枚举不可用），为避免双实例，本次拒绝启动。"
              "请稍后重试或用 --stop 清理。" % pid)
        return 2
    if ident is True and pid != os.getpid():
        print("已有守护在运行（PID %d），拒绝重复启动。" % pid)
        return 2
    if pid and ident is False:
        print("清理过期守护 PID 文件（原记录 PID %d 已不是本守护）。" % pid)
        try:
            os.remove(PID_FILE)
        except Exception:
            pass

    state, detail = probe()
    if args.dry_run:
        print("=== 守护脚本体检 ===")
        print("Python        : %s" % PY)
        print("子进程脚本    : %s%s" % (CHILD, "（存在）" if os.path.exists(os.path.join(ROOT, CHILD)) else "（缺失！）"))
        print("子进程参数    : %s" % " ".join(CHILD_ARGS))
        print("队列文件      : %s（剩余 %s 条）" % (LIST_FILE, remaining()))
        print("已落盘篇数    : %d" % count_posts())
        print("零增量退出    : 连续 %d 轮" % args.max_idle_rounds)
        print("日志/PID      : %s" % LOG_FILE)
        print("抓取进程      : %s" % {"yes": "运行中（%d 个）" % len(detail),
                                      "no": "无",
                                      "unknown": "无法确认（%s）" % detail}[state])
        if state == "yes":
            print("接管策略      : 已存在抓取进程 → 守护只等待不启动，待其退出后自动接管。")
        elif state == "no":
            print("接管策略      : 已确认无抓取进程 → 守护将拉起一轮。")
        else:
            print("接管策略      : 探测结果不可信 → 按“有”处理，守护只等待不启动（fail-closed）。")
        print("结论：探测为 fail-closed，任何不确定都不会导致双实例并发。")
        return 0

    if args.detach:
        # 单实例闸（2026-10-25）：不再只看 PID 文件，而是直接问"还有谁在跑守护"。
        # 无法确认时 fail-closed（拒绝启动，宁可漏动、不可双开）。
        sib = _other_watchdogs()
        if sib is None:
            print("无法确认是否已有守护在运行（进程枚举不可信），为避免双实例，本次拒绝启动。")
            return 2
        if sib:
            print("已有守护在运行（PID %s），拒绝重复启动。" % ", ".join(str(p) for p, _ in sib))
            return 2
        base = 0x00000008 | 0x00000200          # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        BREAKAWAY = 0x01000000                  # CREATE_BREAKAWAY_FROM_JOB
        argv = [PY, os.path.abspath(__file__),
                "--pause", str(args.pause),
                "--poll", str(args.poll),
                "--max-idle-rounds", str(args.max_idle_rounds)]
        # 事故教训（2026-10-24 16:29）：守护与子进程同时"无日志、无崩溃栈"消失，
        # 抓取静默停摆 95 分钟 —— 典型特征是宿主作业对象(job)关闭时连带清理进程树。
        # 因此优先尝试脱离 job（CREATE_BREAKAWAY_FROM_JOB）；若该 job 不允许脱离，
        # CreateProcess 会直接报错，再回退为普通后台启动，保证仍能起来。
        started = False
        for flags in (base | BREAKAWAY, base):
            try:
                subprocess.Popen(argv, cwd=ROOT, creationflags=flags,
                                 stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL,
                                 close_fds=True)
                started = True
                if flags & BREAKAWAY:
                    print("守护已后台启动（已脱离宿主作业对象，不易被连带清理）。")
                else:
                    print("守护已后台启动（脱离作业对象失败，已回退为普通后台启动）。")
                break
            except OSError as e:
                if flags & BREAKAWAY:
                    print("脱离作业对象失败（%s），改用普通后台启动重试。" % e)
                else:
                    print("后台启动失败：%s" % e)
                    return 3
        if not started:
            return 3
        print("查看状态：python tools/watchdog_run.py --status")
        print("请求停止：python tools/watchdog_run.py --stop")
        return 0

    # ---------- 前台守护主循环 ----------
    # 单实例闸：开工前先问一遍"还有谁在跑守护"（不依赖 PID 文件），
    # 写入 PID 文件后再复查一次，防止两个守护同时启动的竞态。
    sib = _other_watchdogs()
    if sib is None:
        log("无法确认是否已有守护在运行（进程枚举不可信）→ fail-closed，本进程退出，"
            "不冒险造成双实例。")
        return 2
    if sib:
        log("已有守护在运行（PID %s）→ 本进程退出，坚持单实例。"
            % ", ".join(str(p) for p, _ in sib))
        return 2
    with io.open(PID_FILE, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    sib2 = _other_watchdogs()
    if sib2:
        # 竞态：另有守护也在启动。让给对方，并只在 PID 文件确实是自己写的时才清理，
        # 避免把对方的 PID 文件删掉（否则下次启动又会误判"过期"而重复拉起）。
        log("启动竞态：发现另一守护（PID %s）同时启动 → 本进程主动让位退出。"
            % ", ".join(str(p) for p, _ in sib2))
        try:
            if read_pid() == os.getpid():
                os.remove(PID_FILE)
        except Exception:
            pass
        return 2
    if sib2 is None:
        log("写入 PID 文件后无法复查守护身份（进程枚举不可信）→ 为稳妥起见本进程退出。")
        try:
            if read_pid() == os.getpid():
                os.remove(PID_FILE)
        except Exception:
            pass
        return 2
    if os.path.exists(STOP_FLAG):
        os.remove(STOP_FLAG)
    log("守护启动 PID=%d，子进程：%s %s" % (os.getpid(), CHILD, " ".join(CHILD_ARGS)))
    log("两轮间隔 %.0f 秒；等待外部进程轮询 %.0f 秒；连续 %d 轮零增量则退出。"
        % (args.pause, args.poll, args.max_idle_rounds))

    child = None
    child_out = child_err = None
    t0 = 0.0
    rounds = 0
    idle_rounds = 0
    prev = count_posts()
    waited_note = False

    try:
        while True:
            # ---- 分支一：当前没有子进程 → 决定是否拉起 ----
            if child is None:
                if os.path.exists(STOP_FLAG):
                    log("检测到 STOP 旗标，守护退出。")
                    break
                left = remaining()
                if left == 0:
                    log("队列剩余为 0，全部处理完毕，守护退出。")
                    break
                state, detail = probe()
                if state == "unknown":
                    log("【拒绝启动】无法确认是否已有抓取进程（%s）→ 按“有”处理，%d 秒后重试。"
                        % (detail, int(args.poll)))
                    time.sleep(args.poll)
                    continue
                if state == "yes":
                    if not waited_note:
                        log("检测到已有抓取进程在运行，守护进入等待接管（不重复启动）：")
                        for r in detail:
                            log("   " + r[:110])
                        waited_note = True
                    time.sleep(args.poll)
                    continue
                age = log_age()
                if age is not None and age < 30:
                    # 进程枚举说"没有"，但进度日志 30 秒内刚被写过 → 信号矛盾，按"有"处理
                    log("【拒绝启动】进程枚举为空，但进度日志 %.0f 秒前刚写入，信号矛盾 → "
                        "%d 秒后重试。" % (age, int(args.poll)))
                    time.sleep(args.poll)
                    continue
                waited_note = False
                rounds += 1
                t0 = time.time()
                child_out = io.open(os.path.join(OUT_DIR, "crawl_stdout_p0.log"), "a",
                                    encoding="utf-8", errors="ignore")
                child_err = io.open(os.path.join(OUT_DIR, "crawl_stderr_p0.log"), "a",
                                    encoding="utf-8", errors="ignore")
                child = subprocess.Popen([PY, CHILD] + CHILD_ARGS, cwd=ROOT,
                                         stdout=child_out, stderr=child_err)
                log("--- 第 %d 轮启动 PID=%d，队列剩余 %s 条 ---" % (rounds, child.pid, left))
                continue

            # ---- 分支二：有子进程 → 监控 ----
            if child.poll() is not None:
                code = child.returncode
                for h in (child_out, child_err):
                    try:
                        if h:
                            h.close()
                    except Exception:
                        pass
                child_out = child_err = None
                child = None
                dur = time.time() - t0
                now = count_posts()
                delta = now - prev
                prev = now
                log("第 %d 轮结束：退出码=%s 耗时=%.0f 秒 新增=%d 篇 累计=%d 篇 队列剩余=%s"
                    % (rounds, code, dur, delta, now, remaining()))
                if os.path.exists(STOP_FLAG):
                    log("检测到 STOP 旗标，守护退出。")
                    break
                if delta <= 0:
                    idle_rounds += 1
                    log("本轮零增量（连续第 %d 次）。" % idle_rounds)
                    if idle_rounds >= args.max_idle_rounds:
                        log("连续 %d 轮零增量，判定无可抓内容或通道持续不可用，守护退出。"
                            % idle_rounds)
                        break
                else:
                    idle_rounds = 0
                if args.max_rounds and rounds >= args.max_rounds:
                    log("已达 --max-rounds=%d，守护退出。" % args.max_rounds)
                    break
                log("等待 %.0f 秒后进入下一轮..." % args.pause)
                time.sleep(args.pause)
                continue

            if os.path.exists(STOP_FLAG):
                log("收到 STOP 旗标，等待子进程抓完当前篇（最多 %.0f 秒）..." % args.stop_grace)
                try:
                    child.wait(timeout=args.stop_grace)
                except Exception:
                    log("子进程未按时结束，终止它（已抓内容均已落盘，可续跑）。")
                    child.terminate()
                    try:
                        child.wait(timeout=30)
                    except Exception:
                        child.kill()
                continue

            time.sleep(5)
    except KeyboardInterrupt:
        log("收到中断信号，守护退出。")
    finally:
        if child is not None and child.poll() is None:
            log("守护退出前终止子进程（已抓内容均已落盘，可续跑）。")
            try:
                child.terminate()
            except Exception:
                pass
        for h in (child_out, child_err):
            try:
                if h:
                    h.close()
            except Exception:
                pass
        try:
            if read_pid() == os.getpid():
                os.remove(PID_FILE)
        except Exception:
            pass
        log("守护结束。")
    return 0


if __name__ == "__main__":
    sys.exit(main())