# -*- coding: utf-8 -*-
"""已落盘空文复验通道（独立目录落盘，绝不覆盖主库）。

背景与用户决策（2026-10-25）：
  - 主库 output/1216826604_wayback/ 有 67 篇正文 <30 字的产物，本地 raw 已确认救不回，
    只能重取快照；用户要求"**不自动改，只落新文件**"。
  - 主脚本 `wayback_resume.py` 的 `--retry-skip` 只重取"跳过清单里尚未落盘"的条目，
    已落盘的 67 篇被 `a not in done` 天然排除、永不会被覆盖 —— 因此需要本独立通道。

本工具的设计要点：
  1. **不改主脚本**：以库方式 import wayback_resume，仅在本进程内把它的
     POSTS_DIR / RAW_DIR 指向复验目录，主库与主线进程完全不受影响；
  2. **默认试运行**：不加 --apply 只打印计划，不发起任何网络请求；
  3. **串行限速**：与主线同规，间隔默认 15 秒，逐篇轮换中转节点，不并发；
  4. **主线在跑则拒跑**：progress.log 在 180 秒内被写过即判定主线活跃，直接退出
     （需 --force 才可越过，避免两条串行通道同时打 Wayback 造成超频）；
  5. **断点续跑**：已完成的 id 记入 recheck_state.json，重跑自动跳过。
"""
import argparse
import glob
import io
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import wayback_resume as W  # noqa: E402

RECHECK_DIR = os.path.join(ROOT, "output", "1216826604_wayback_recheck")
R_POSTS = os.path.join(RECHECK_DIR, "posts")
R_RAW = os.path.join(RECHECK_DIR, "raw")
R_LOG = os.path.join(RECHECK_DIR, "recheck.log")
R_STATE = os.path.join(RECHECK_DIR, "recheck_state.json")
REQUEUE = os.path.join(ROOT, "temp", "requeue_ids.json")


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line)
    sys.stdout.flush()
    try:
        io.open(R_LOG, "a", encoding="utf-8").write(line + "\n")
    except Exception:
        pass


def main_crawler_active(window=180):
    """主线是否活跃。

    只用 progress.log 的修改时间**不够**：它只在每轮开头/结尾写入，
    长时间轮次中间可能几十分钟不更新，会造成"误判为未活跃"→ 并发抓取 → 超频。
    因此叠加第二个证据：巡检日志 heal.log 的最近一次巡检结论里是否报"抓取进程 1 个"。
    两个证据任一成立即判定活跃（保守优先）。
    """
    reasons = []
    try:
        if (time.time() - os.path.getmtime(W.LOG_FILE)) < window:
            reasons.append("progress.log %d 秒内有写入" % (time.time() - os.path.getmtime(W.LOG_FILE)))
    except Exception:
        pass
    heal = os.path.join(W.OUT_DIR, "heal.log")
    try:
        if (time.time() - os.path.getmtime(heal)) < 900:
            lines = io.open(heal, encoding="utf-8", errors="ignore").read().strip().split("\n")
            for ln in reversed(lines[-20:]):
                if "抓取进程" in ln:
                    if "抓取进程 1 个" in ln:
                        reasons.append("巡检最近报告：抓取进程 1 个")
                    break
    except Exception:
        pass
    return reasons


def load_targets(kind="all"):
    """按类型装载复验目标。

    kind: landed_empty（已落盘空文）| skipped（永久跳过清单）| all
    两类目标都从 temp/requeue_ids.json 读取（已冻结的任务单），
    url / timestamp 从 CDX 索引清单补齐。
    """
    doc = json.load(io.open(REQUEUE, encoding="utf-8"))
    if kind == "all":
        items = list(doc["items"])
    else:
        items = [x for x in doc["items"] if x.get("kind") == kind]
    data = W.load_json(W.LIST_FILE, {}).get("items", {})
    ready, missing = [], []
    for x in items:
        aid = x["article_id"]
        v = data.get(aid)
        if v and v.get("timestamp") and v.get("url"):
            ready.append({"article_id": aid, "timestamp": v["timestamp"], "url": v["url"],
                          "kind": x.get("kind", "unknown"), "reason": x.get("reason", "")})
        else:
            missing.append("%s(%s)" % (aid, x.get("kind", "?")))
    return ready, missing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正发起抓取（默认仅试运行）")
    ap.add_argument("--limit", type=int, default=5, help="本次最多重取篇数（默认 5，先小批探路）")
    ap.add_argument("--gap", type=float, default=15.0, help="篇间间隔秒数（默认 15，勿低于 10）")
    ap.add_argument("--force", action="store_true", help="主线活跃时也强制执行（危险）")
    ap.add_argument("--kind", default="all",
                    choices=["all", "landed_empty", "skipped"],
                    help="复验范围：all=两类都做（默认）；landed_empty=仅已落盘空文；"
                         "skipped=仅永久跳过清单")
    args = ap.parse_args()

    with io.open(os.path.join(RECHECK_DIR, "STAGING.md"), encoding="utf-8"):
        pass

    ready, missing = load_targets(args.kind)
    state = W.load_json(R_STATE, {})
    left = [x for x in ready if x["article_id"] not in state]
    n_empty = sum(1 for x in ready if x["kind"] == "landed_empty")
    log("复验通道 | 范围 %s | 目标 %d 条（已落盘空文 %d / 跳过清单 %d；清单缺条目 %d）"
        "| 已完成 %d | 待办 %d"
        % (args.kind, len(ready), n_empty, len(ready) - n_empty,
           len(missing), len(state), len(left)))
    active = main_crawler_active()
    log("主线活跃判据：%s" % ("；".join(active) if active else "两条证据均未命中，判定未活跃"))

    if missing:
        log("清单缺条目（无 url/时间戳，需先补 CDX 索引）：%d 条 %s"
            % (len(missing), missing[:5]))

    todo = left[:args.limit]
    if not todo:
        log("无待办，退出")
        return
    log("本次计划 %d 篇，预计耗时 %.1f 分钟（间隔 %.0f 秒/篇）"
        % (len(todo), len(todo) * args.gap / 60.0, args.gap))
    for x in todo:
        log("  %s  %s  %s" % (x["article_id"], x["timestamp"], x["url"]))

    if not args.apply:
        log("试运行结束（未发起任何请求）。加 --apply 才会真正抓取。")
        return
    if active and not args.force:
        log("主线正在抓取，拒绝执行：两条串行通道同时打 Wayback 会触发超频。"
            "等主线排空后再跑，或确认后加 --force。")
        return

    for dd in (R_POSTS, R_RAW):
        if not os.path.isdir(dd):
            os.makedirs(dd)
    # 仅在**本进程内**重定向输出目录，主脚本文件与主线进程均不受影响
    W.POSTS_DIR, W.RAW_DIR = R_POSTS, R_RAW
    W.load_nodes()

    i = 0
    for n, x in enumerate(todo):
        aid = x["article_id"]
        name, tmpl = W.NODES[i % len(W.NODES)]
        i += 1
        if n:
            time.sleep(args.gap)
        try:
            html, delivered, wrapped = W.fetch_one(name, tmpl, x["timestamp"], x["url"])
            status, p = W.parse_article(html) if html else ("no_body", None)
        except Exception as e:
            log("  失败(%s) %s: %s" % (name, aid, str(e)[:70]))
            continue
        if status not in ("ok", "photo_only") or not p:
            log("  仍无正文(%s) %s -> %s" % (name, aid, status))
            state[aid] = {"result": status, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
            W.save_json(R_STATE, state)
            continue
        fp = os.path.join(R_POSTS, aid + ".md")
        with io.open(fp, "w", encoding="utf-8") as f:
            f.write("---\n")
            f.write("article_id: %s\n" % aid)
            f.write("title: %s\n" % p["title"])
            f.write("pub_time: %s\n" % p["pub_time"])
            f.write("source: archive.org snapshot\n")
            f.write("snapshot_ts: %s\n" % x["timestamp"])
            f.write("source_url: %s\n" % x["url"])
            f.write("origin: wayback\n")
            f.write("stage: recheck\n")
            f.write("recheck: 1\n")
            f.write("recheck_kind: %s\n" % x.get("kind", ""))
            f.write("parse_rev: %s\n" % W.PARSE_REV)
            raw_rel, raw_hash = W.save_raw(aid, x["timestamp"], delivered)
            if raw_rel:
                f.write("raw_file: %s\nraw_sha1_b32: %s\n" % (raw_rel, raw_hash))
            f.write("---\n\n%s\n" % p["content"])
        state[aid] = {"result": status, "chars": len(p["content"]),
                      "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        W.save_json(R_STATE, state)
        log("  成功(%s) %s：%d 字 -> %s" % (name, aid, len(p["content"]), fp))
    log("本次结束。累计完成 %d / %d" % (len(state), len(ready) + len(missing)))


if __name__ == "__main__":
    main()