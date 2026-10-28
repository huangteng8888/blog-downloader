# -*- coding: utf-8 -*-
"""
wu2198 补抓 · 自愈巡检（无人值守，由 Windows 计划任务每 10 分钟调用一次）

存在理由（2026-10-24 事故）：
  16:29 守护与抓取子进程被宿主整体终止，两者都没留下任何收尾日志；
  而 watchdog.pid 残留 + 旧的单实例判断把死 PID 当活进程，导致连人工重启都被
  "已有守护在运行"拒绝 —— 抓取静默停摆 95 分钟，零产出，无人发现。
  本脚本补上缺失的一环：**定期自检 + 停滞即拉起 + 停滞留痕**。
  由 Task Scheduler 启动，不在宿主的进程树内，因此宿主清理会话不会连它一起清掉。

判定表（宁可漏动，不可双开 —— 与守护同一 fail-closed 立场）：
  | 抓取进程 | 进度日志年龄 | 动作 |
  | 有       | <= STALL_SEC | 正常，不动 |
  | 有       | >  STALL_SEC | 疑似挂起：写告警，**不**启动第二个 |
  | 无       | <= START_GRACE | 疑似正在换轮，等下一次巡检 |
  | 无       | >  START_GRACE | **停滞：写告警 + 拉起守护** |
  | 无法确认 | -            | fail-closed：写告警，不动 |

用法：
    python tools/self_heal.py            # 巡检一次（计划任务用）
    python tools/self_heal.py --status   # 只看状态，不启动任何进程
"""
import argparse
import glob
import io
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "output", "1216826604_wayback")
POSTS_DIR = os.path.join(OUT_DIR, "posts")
PROG_LOG = os.path.join(OUT_DIR, "progress.log")
LIST_FILE = os.path.join(ROOT, "temp", "wayback_cdx2.json")
SKIP_FILE = os.path.join(OUT_DIR, "skip_permanent.json")
HEAL_LOG = os.path.join(OUT_DIR, "heal.log")
ALERT_MD = os.path.join(OUT_DIR, "ALERT.md")
STATUS_JSON = os.path.join(OUT_DIR, "heal_status.json")
STOP_FLAG = os.path.join(OUT_DIR, "STOP")
WATCHDOG = os.path.join(ROOT, "tools", "watchdog_run.py")

CHILD_KEY = "wayback_resume.py"
GUARD_KEY = "watchdog_run.py"

STALL_SEC = 600.0        # 进度日志久于此值未更新 → 视为停滞/挂起
START_GRACE = 120.0      # 无抓取进程但日志在此秒数内写过 → 可能正在换轮，先不动
DRY = False              # dry-run：只判定，不动系统、不写告警

# DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB
FLAGS_BREAKAWAY = 0x00000008 | 0x00000200 | 0x01000000
FLAGS_PLAIN = 0x00000008 | 0x00000200


def log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        print(line)
    except Exception:
        pass          # 隐藏窗口下 stdout 编码可能受限，日志靠文件通道即可
    try:
        with io.open(HEAL_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    try:
        sys.stdout.flush()
    except Exception:
        pass


def ps_lines():
    """列出所有 python.exe / pythonw.exe 的命令行；失败返回 None（调用方据此 fail-closed）。

    2026-10-25：过滤条件补上 pythonw.exe。计划任务用 pythonw.exe 拉起本脚本，
    它再用 sys.executable 拉起守护，因此**生产环境里的守护是 pythonw.exe**；
    只过滤 python.exe 会让自愈看不到自己拉起的守护，每 10 分钟重复拉起一个。
    """
    cmd = ("Get-CimInstance Win32_Process -Filter \"name='python.exe' or name='pythonw.exe'\" | "
           "ForEach-Object { $_.ProcessId.ToString() + '|' + $_.CommandLine }")
    try:
        p = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                           capture_output=True, timeout=90)
    except Exception:
        return None
    if p.returncode != 0:
        return None
    out = p.stdout.decode("utf-8", "ignore") or p.stdout.decode("gbk", "ignore")
    rows = []
    for ln in out.splitlines():
        ln = ln.strip()
        if "|" in ln:
            pid, cl = ln.split("|", 1)
            try:
                rows.append((int(pid.strip()), cl))
            except ValueError:
                pass
    return rows


def probe():
    """返回 (state, detail)：state ∈ {yes,no,unknown}。"""
    rows = ps_lines()
    if rows is None:
        return "unknown", "进程枚举失败"
    me = os.getpid()
    kids = [(p, c) for p, c in rows if CHILD_KEY in c and p != me]
    guards = [(p, c) for p, c in rows if GUARD_KEY in c and p != me]
    return ("yes" if kids else "no"), (kids, guards)


def count_posts():
    return len(glob.glob(os.path.join(POSTS_DIR, "*.md")))


def log_age():
    try:
        return time.time() - os.path.getmtime(PROG_LOG)
    except Exception:
        return None


def remaining():
    """剩余待抓篇数（与抓取脚本同一口径：清单 items 中既未落盘、也未永久跳过的条数）。

    为什么要它：任务全部跑完后，抓取进程自然不存在、进度日志也不再增长，
    纯按"无进程 + 日志超时"判定就会把"已完成"误判成"停滞"，每 10 分钟
    拉起一个立刻退出的空跑轮次，并向 ALERT.md 灌入假告警。

    坑（2026-10-24 实测）：清单文件 temp/wayback_cdx2.json 是**嵌套字典**，
    文章在 "items" 键下，顶层只有 3 个键。早先按 len(整个文件) 相减，得到
    "剩余 -1890 篇"这种负数 —— 若采信它，一旦抓取真停滞，自愈会误判
    "已全部处理完"而**拒绝重启**，等于亲手关掉自愈。故此处严格照抄
    tools/wayback_resume.py 的加载方式（data = load_json(LIST_FILE).get("items")），
    并在调用侧对负数/异常一律降级为「未知」。
    """
    try:
        with io.open(LIST_FILE, encoding="utf-8") as f:
            items = json.load(f).get("items", {})
        with io.open(SKIP_FILE, encoding="utf-8") as f:
            skips = json.load(f)
    except Exception:
        return None
    if not items:
        return None
    done = {os.path.basename(f)[:-3]
            for f in glob.glob(os.path.join(POSTS_DIR, "*.md"))}
    return len([a for a in items if a not in done and a not in skips])


def append_alert(title, detail):
    """把告警追加到 ALERT.md（供人查看，不做任何系统通知）。"""
    if DRY:
        return
    try:
        new = not os.path.exists(ALERT_MD)
        with io.open(ALERT_MD, "a", encoding="utf-8") as f:
            if new:
                f.write("# 抓取停滞告警\n\n")
                f.write("本文件由 `tools/self_heal.py` 自动追加。**出现新条目即说明曾发生停滞，需人工确认。**\n\n")
                f.write("| 记录时间 | 事件 | 详情 |\n| --- | --- | --- |\n")
            f.write("| %s | %s | %s |\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), title,
                                           detail.replace("|", "/")))
    except Exception as e:
        log("写告警文件失败：%s" % e)


def decode_out(b):
    """解码子进程输出。

    2026-10-25 事故复盘缺陷：此处原先固定 utf-8 + ignore，而 pythonw 子进程的
    中文输出走控制台代码页（cp936），于是整段中文变成 "?"——现场只留下
    "拉起结果：? PID ??? PID 11036 ???"，无法判读到底成功还是失败。
    改为优先 utf-8、失败退 cp936，均以 replace 兜底，保证可判读。
    """
    for enc in ("utf-8", "cp936"):
        try:
            return b.decode(enc)
        except Exception:
            continue
    return b.decode("utf-8", "replace")


def start_watchdog():
    """经 watchdog_run.py --detach 拉起守护（它自身会用 breakaway 脱离作业对象）。"""
    argv = [sys.executable, WATCHDOG, "--detach",
            "--pause", "60", "--poll", "30", "--max-idle-rounds", "20"]
    try:
        p = subprocess.run(argv, cwd=ROOT, capture_output=True, timeout=180)
    except Exception as e:
        return False, "拉起失败：%s" % e
    txt = decode_out((p.stdout or b"") + b"\n" + (p.stderr or b"")).strip()
    txt = " ".join(txt.split())          # 折行与多余空白压缩，避免日志被撑散
    return (p.returncode == 0), (txt or "退出码 %d" % p.returncode)


def save_status(d):
    """写巡检状态文件。dry-run 一律不写。

    2026-10-25：此前 save_status 未受 DRY 约束，用 temp/test_heal_branch.py 做离线
    分支验证时，测试用的假状态（抓取 0 个/守护 0 个）被写进了生产状态文件
    heal_status.json，会误导后续查看者与报表。凡"只做判定"的运行都不得改动状态。
    """
    if DRY:
        return
    try:
        tmp = STATUS_JSON + ".tmp"
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, STATUS_JSON)
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true", help="只看状态，不启动任何进程")
    ap.add_argument("--dry-run", action="store_true",
                    help="只做判定，不真正拉起守护、不写告警（用于验证判定分支）")
    args = ap.parse_args()
    global DRY
    DRY = args.dry_run

    posts = count_posts()
    age = log_age()
    rem = remaining()
    if rem is not None and rem < 0:
        log("提示：剩余篇数算出负数（%d），清单口径可能不一致，本次降级为「未知」，"
            "不作为完成判定依据" % rem)
        rem = None
    state, detail = probe()
    age_s = "未知" if age is None else "%.0f 秒前" % age

    if state == "unknown":
        kids, guards = [], []
    else:
        kids, guards = detail

    log("巡检：已落盘 %d 篇 | 剩余 %s | 进度日志 %s | 抓取进程 %d 个 | 守护 %d 个"
        % (posts, "未知" if rem is None else "%d 篇" % rem, age_s, len(kids), len(guards)))

    action = "无"
    if os.path.exists(STOP_FLAG):
        action = "检测到 STOP 旗标，尊重停止请求，不启动"
        log("  → " + action)
    elif state == "unknown":
        action = "无法确认进程状态（fail-closed，不启动）"
        log("  → " + action)
        append_alert("进程枚举失败", "无法确认是否有抓取进程，已跳过本轮自愈（避免双开）")
    elif kids:
        if age is not None and age > STALL_SEC:
            action = "有抓取进程但进度日志超时，疑似挂起（不启动第二个）"
            log("  → " + action)
            append_alert("疑似挂起", "有抓取进程但进度日志已 %.0f 秒无更新（阈值 %.0f）"
                                    % (age, STALL_SEC))
        else:
            action = "正常"
    else:
        if rem == 0:
            action = "清单已全部处理完（剩余 0 篇），无需启动"
            log("  → " + action)
        elif guards:
            # 2026-10-25 新增：无抓取进程但已有守护在场 —— 这是守护的职责范围，自愈
            # 不得再拉起第二个。旧实现在此分支完全不看 guards，配合"pythonw 守护不可见"
            # 的枚举缺陷，每 10 分钟就制造一个重复守护（曾累积到 4 个）。
            gids = ", ".join(str(p) for p, _ in guards)
            stall = "未知" if age is None else "%.0f 分钟" % (age / 60.0)
            action = "无抓取进程，但已有 %d 个守护在场（PID %s），交给守护自行恢复，不重复拉起" \
                     % (len(guards), gids)
            log("  → %s（停滞约 %s，当前 %d 篇）" % (action, stall, posts))
            append_alert("守护在场但抓取进程缺失",
                         "停滞约 %s（%d 篇）；在场守护 PID %s，由守护负责恢复，本轮不重复拉起"
                         % (stall, posts, gids))
        elif age is not None and age <= START_GRACE:
            action = "暂无抓取进程但日志刚写过，疑似换轮，下次巡检再判"
            log("  → " + action)
        else:
            stall = "未知" if age is None else "%.0f 分钟" % (age / 60.0)
            if args.dry_run:
                action = "（dry-run）判定为停滞，此处本应拉起守护"
                log("  → %s（停滞约 %s，当前 %d 篇）" % (action, stall, posts))
            else:
                action = "检测到停滞，拉起守护"
                log("  → %s（停滞约 %s，当前 %d 篇）" % (action, stall, posts))
                ok, info = start_watchdog()
                if ok:
                    append_alert("停滞已自愈", "停滞约 %s（%d 篇），已拉起守护" % (stall, posts))
                    log("     拉起结果：%s" % info)
                else:
                    append_alert("停滞且自愈失败", "停滞约 %s（%d 篇）；拉起失败：%s"
                                                  % (stall, posts, info))
                    log("     拉起失败：%s" % info)
                    action = "停滞，且拉起失败"

    save_status({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "posts": posts,
                 "remaining": rem,
                 "log_age_sec": None if age is None else round(age, 1),
                 "crawlers": len(kids), "guards": len(guards),
                 "state": state, "action": action})
    log("巡检结论：%s" % action)
    return 0


if __name__ == "__main__":
    sys.exit(main())