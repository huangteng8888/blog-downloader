# -*- coding: utf-8 -*-
"""为 tools/wayback_resume.py 打"状态即时落盘"补丁（persist-on-write）。

背景（2026-10-24 实测）：
    原实现只在**每轮抓取结束后**才把 skip / transient / warnings 写入 JSON。
    轮中被中断（到点退出、被杀进程）会丢失本轮记录，下轮重复抓取这些文章；
    实测 no_body 类约占 6%，属于纯浪费。

本补丁做什么：
    1) 新增 save_json_atomic / persist_skips / persist_transient / persist_warnings；
    2) 在 SKIPPED / TRANSIENT / WARNINGS ��处 append 之后立即落盘；
    3) 轮末"合并落盘"块改为调用同一批函数（避免覆盖已即时写入的记录）。

安全设计：
    - 幂等：以 PATCH_REV 标记判断是否已打过补丁，重复执行直接跳过；
    - 断言：每个锚点必须**恰好命中一次**，否则整体放弃（不改文件）；
    - 备份：改写前备份到 tools/_backup/；
    - 校验：改写后 py_compile + 导入 + 沙箱功能自测，任一失败即自动回滚；
    - 择机：默认等到**没有抓取进程在跑**时才动手（--now 可跳过等待）。

用法：
    python tools/apply_resume_patch.py --dry-run              # 只看锚点是否命中
    python tools/apply_resume_patch.py --target temp/x.py     # 对副本试打
    python tools/apply_resume_patch.py --wait                 # 等空闲再打（守护用）
    python tools/apply_resume_patch.py --now                  # 立即打
"""
import argparse
import importlib.util
import io
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET_DEFAULT = os.path.join(ROOT, "tools", "wayback_resume.py")
BACKUP_DIR = os.path.join(ROOT, "tools", "_backup")
PATCH_LOG = os.path.join(ROOT, "output", "1216826604_wayback", "patch_apply.log")
CHILD_KEY = "wayback_resume"

MARKER = "PATCH_REV = \"persist-on-write-20261024\""


def log(m):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), m)
    print(line)
    sys.stdout.flush()
    try:
        d = os.path.dirname(PATCH_LOG)
        if not os.path.isdir(d):
            os.makedirs(d)
        with io.open(PATCH_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def crawlers():
    """返回外部抓取进程 (pid, 命令行) 列表。

    fail-closed（2026-10-24 事故后修订）：查询失败/结果不可信时返回 None，
    调用方必须把 None 当成"可能正在抓取"而继续等待，绝不能当成"没有抓取进程"。
    """
    ps = ("Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | "
          "ForEach-Object { $_.ProcessId.ToString() + '|' + $_.CommandLine }")
    try:
        p = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           timeout=60)
        if p.returncode != 0:
            return None
        out = p.stdout or b""
    except Exception:
        return None
    txt = out.decode("utf-8", "ignore")
    if "|" not in txt:
        alt = out.decode("utf-16", "ignore")
        if "|" in alt:
            txt = alt
    res = []
    allpids = set()
    for ln in txt.splitlines():
        ln = ln.strip()
        if "|" not in ln:
            continue
        pid, cmd = ln.split("|", 1)
        if not pid.strip().isdigit():
            continue
        allpids.add(int(pid.strip()))
        if CHILD_KEY in cmd:
            res.append((int(pid.strip()), cmd.strip()))
    if os.getpid() not in allpids:
        return None            # 枚举自检失败：连自身都没查到，结果不可信
    return res


# ---------------------------------------------------------------- 补丁内容

HELPERS = '''def save_json_atomic(path, obj):
    """原子写：先写同目录 .tmp 再 os.replace，避免中途中断留下半个文件。"""
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def persist_skips():
    """永久跳过清单即时落盘（累积，不覆盖历史）。"""
    if _CACHE["skip"] is None:
        _CACHE["skip"] = load_json(SKIP_FILE, {})
    d = _CACHE["skip"]
    if not isinstance(d, dict):
        d = {}
    for s in SKIPPED[_PERSISTED["skip"]:]:
        d[s["article_id"]] = {"url": s["url"], "reason": s["reason"],
                              "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    _PERSISTED["skip"] = len(SKIPPED)
    save_json_atomic(SKIP_FILE, d)


def persist_transient():
    """临时失败清单即时落盘（累积去重，便于评估通道健康度）。"""
    if _CACHE["transient"] is None:
        _CACHE["transient"] = load_json(FAIL_FILE, [])
    d = _CACHE["transient"]
    if not isinstance(d, list):
        d = []
    seen = set(r.get("article_id") for r in d if isinstance(r, dict))
    for r in TRANSIENT[_PERSISTED["transient"]:]:
        if r["article_id"] not in seen:
            d.append(r)
            seen.add(r["article_id"])
    _PERSISTED["transient"] = len(TRANSIENT)
    save_json_atomic(FAIL_FILE, d)


def persist_warnings():
    """异常样本清单即时落盘（累积去重）。"""
    if _CACHE["warn"] is None:
        _CACHE["warn"] = load_json(WARN_FILE, [])
    d = _CACHE["warn"]
    if not isinstance(d, list):
        d = []
    seen = set(r.get("article_id") for r in d if isinstance(r, dict))
    for r in WARNINGS[_PERSISTED["warn"]:]:
        if r["article_id"] not in seen:
            d.append(r)
            seen.add(r["article_id"])
    _PERSISTED["warn"] = len(WARNINGS)
    save_json_atomic(WARN_FILE, d)


'''

HELPER_HEADER = '''# ---- 状态即时落盘（2026-10-24 补丁）----
# 原实现只在每轮结束后才写 skip/transient/warnings；轮中被中断会丢失本轮记录，
# 导致这些文章下轮被重复抓取。改为记录一旦产生立即原子落盘，格式与原来一致。
PATCH_REV = "persist-on-write-20261024"
_PERSISTED = {"skip": 0, "transient": 0, "warn": 0}
_CACHE = {"skip": None, "transient": None, "warn": None}


'''

OLD_MERGE = [
    "    # 合并落盘",
    "    old_skip = load_json(SKIP_FILE, {})",
    "    for s in SKIPPED:",
    "        old_skip[s[\"article_id\"]] = {\"url\": s[\"url\"], \"reason\": s[\"reason\"],",
    "                                     \"at\": time.strftime(\"%Y-%m-%d %H:%M:%S\")}",
    "    with io.open(SKIP_FILE, \"w\", encoding=\"utf-8\") as f:",
    "        json.dump(old_skip, f, ensure_ascii=False, indent=1)",
    "    with io.open(FAIL_FILE, \"w\", encoding=\"utf-8\") as f:",
    "        json.dump(TRANSIENT, f, ensure_ascii=False, indent=1)",
    "    with io.open(WARN_FILE, \"w\", encoding=\"utf-8\") as f:",
    "        json.dump(WARNINGS, f, ensure_ascii=False, indent=1)",
]

NEW_MERGE = [
    "    # 合并落盘（每篇已即时落盘，此处仅作最终一致性写入，避免覆盖）",
    "    persist_skips()",
    "    persist_transient()",
    "    persist_warnings()",
    "    old_skip = load_json(SKIP_FILE, {})",
]


def apply_patch(text, nl):
    """返回 (新文本, 命中说明)。锚点必须恰好命中一次，否则抛异常。"""
    hits = []

    def sub_once(t, anchors, new, name):
        old = nl.join(anchors)
        n = t.count(old)
        if n != 1:
            raise RuntimeError("锚点[%s]命中 %d 次（应为 1 次），已放弃修改" % (name, n))
        hits.append(name)
        return t.replace(old, nl.join(new))

    # 1) 在 load_json 之后插入辅助函数
    t = sub_once(
        text,
        ["def load_json(path, default):",
         "    try:",
         "        with io.open(path, encoding=\"utf-8\") as f:",
         "            return json.load(f)",
         "    except Exception:",
         "        return default"],
        ["def load_json(path, default):",
         "    try:",
         "        with io.open(path, encoding=\"utf-8\") as f:",
         "            return json.load(f)",
         "    except Exception:",
         "        return default",
         "",
         ""]
        + HELPER_HEADER.split("\n")[:-1] + HELPERS.split("\n")[:-1],
        "insert_helpers")

    # 2) 三处 append 之后立即落盘
    t = sub_once(
        t,
        ["            TRANSIENT.append({\"article_id\": aid, \"node\": name, \"error\": msg})",
         "            STATS[\"transient\"] += 1"],
        ["            TRANSIENT.append({\"article_id\": aid, \"node\": name, \"error\": msg})",
         "            persist_transient()",
         "            STATS[\"transient\"] += 1"],
        "hook_transient")

    t = sub_once(
        t,
        ["                WARNINGS.append({\"article_id\": aid, \"len\": len(p[\"content\"]),",
         "                                 \"note\": \"正文超长，需人工确认是否混入多篇\"})"],
        ["                WARNINGS.append({\"article_id\": aid, \"len\": len(p[\"content\"]),",
         "                                 \"note\": \"正文超长，需人工确认是否混入多篇\"})",
         "                persist_warnings()"],
        "hook_warnings")

    t = sub_once(
        t,
        ["            SKIPPED.append({\"article_id\": aid, \"url\": v[\"url\"], \"reason\": status})",
         "            STATS[\"skip\"] += 1"],
        ["            SKIPPED.append({\"article_id\": aid, \"url\": v[\"url\"], \"reason\": status})",
         "            persist_skips()",
         "            STATS[\"skip\"] += 1"],
        "hook_skip")

    # 3) 轮末合并块改为调用同一批函数
    t = sub_once(t, OLD_MERGE, NEW_MERGE, "replace_merge")
    return t, hits


# ---------------------------------------------------------------- 校验

def verify(path):
    """编译 + 导入 + 沙箱功能自测。返回 (ok, 说明)"""
    try:
        py_compile.compile(path, cfile=os.path.join(tempfile.gettempdir(), "_wc_check.pyc"),
                           doraise=True)
    except Exception as e:
        return False, "编译失败: %s" % e

    sand = tempfile.mkdtemp(prefix="wd_persist_")
    try:
        spec = importlib.util.spec_from_file_location("_patched_resume", path)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
    except Exception as e:
        return False, "导入失败: %s" % e

    if not hasattr(m, "persist_skips"):
        return False, "缺少 persist_skips（补丁未生效？）"

    m.SKIP_FILE = os.path.join(sand, "skip.json")
    m.FAIL_FILE = os.path.join(sand, "fail.json")
    m.WARN_FILE = os.path.join(sand, "warn.json")
    m._CACHE.update({"skip": None, "transient": None, "warn": None})
    m._PERSISTED.update({"skip": 0, "transient": 0, "warn": 0})
    m.SKIPPED[:] = [{"article_id": "t1", "url": "u1", "reason": "no_body"}]
    m.TRANSIENT[:] = [{"article_id": "t2", "node": "nocors", "error": "timeout"}]
    m.WARNINGS[:] = [{"article_id": "t3", "len": 40000, "note": "x"}]
    try:
        m.persist_skips()
        m.persist_transient()
        m.persist_warnings()
        import json
        skip = json.load(io.open(m.SKIP_FILE, encoding="utf-8"))
        fail = json.load(io.open(m.FAIL_FILE, encoding="utf-8"))
        warn = json.load(io.open(m.WARN_FILE, encoding="utf-8"))
        assert skip["t1"]["reason"] == "no_body", skip
        assert fail[0]["article_id"] == "t2", fail
        assert warn[0]["article_id"] == "t3", warn
        # 再追加一条，验证增量与去重
        m.SKIPPED.append({"article_id": "t4", "url": "u4", "reason": "permission"})
        m.TRANSIENT.append({"article_id": "t2", "node": "allallow", "error": "again"})
        m.persist_skips()
        m.persist_transient()
        skip = json.load(io.open(m.SKIP_FILE, encoding="utf-8"))
        fail = json.load(io.open(m.FAIL_FILE, encoding="utf-8"))
        assert set(skip) == {"t1", "t4"}, skip
        assert len(fail) == 1, fail
    except Exception as e:
        return False, "功能自测失败: %s" % e
    finally:
        shutil.rmtree(sand, ignore_errors=True)
    return True, "编译/导入/功能自测全部通过"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default=TARGET_DEFAULT)
    ap.add_argument("--now", action="store_true", help="不等待空闲，立即执行")
    ap.add_argument("--wait", action="store_true", help="等到无抓取进程再执行（默认行为）")
    ap.add_argument("--dry-run", action="store_true", help="只检查锚点，不改文件")
    ap.add_argument("--poll", type=int, default=5)
    ap.add_argument("--max-wait-hours", type=float, default=14.0)
    args = ap.parse_args()

    tgt = args.target if os.path.isabs(args.target) else os.path.join(ROOT, args.target)
    if not os.path.exists(tgt):
        log("目标文件不存在: %s" % tgt)
        return 1

    raw = io.open(tgt, encoding="utf-8", newline="").read()
    nl = "\r\n" if "\r\n" in raw else "\n"
    text = raw.replace("\r\n", "\n")

    if MARKER in text:
        log("已存在补丁标记，无需重复执行：%s" % os.path.basename(tgt))
        return 0

    if args.dry_run:
        try:
            _, hits = apply_patch(text, "\n")
        except Exception as e:
            log("锚点检查失败：%s" % e)
            return 1
        log("锚点检查通过（共 %d 处）：%s" % (len(hits), ", ".join(hits)))
        return 0

    if not args.now:
        log("等待空闲窗口（无 %s 进程在跑）最长 %.1f 小时..." % (CHILD_KEY, args.max_wait_hours))
        deadline = time.time() + args.max_wait_hours * 3600
        while time.time() < deadline:
            run = crawlers()
            if run is None:
                log("进程探测不可信（fail-closed），继续等待 %d 秒后重试" % int(args.poll))
                time.sleep(args.poll)
                continue
            if not run:
                break
            time.sleep(args.poll)
        else:
            log("等待超时，放弃执行（未改动任何文件）")
            return 2
        log("检测到空闲窗口，开始打补丁")

    try:
        new_text, hits = apply_patch(text, "\n")
    except Exception as e:
        log("打补丁失败：%s" % e)
        return 1

    if not os.path.isdir(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)
    bak = os.path.join(BACKUP_DIR,
                       os.path.basename(tgt).replace(".py", "") + "_20261024_prePersist.py")
    if not os.path.exists(bak):
        shutil.copy2(tgt, bak)
        log("已备份原文件 -> %s" % os.path.relpath(bak, ROOT))

    out = new_text.replace("\n", nl) if nl == "\r\n" else new_text
    # 原子写：防止守护恰好在此刻拉起的新进程读到写了一半的源文件
    tmp = tgt + ".patchtmp"
    with io.open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(out)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, tgt)
    log("已写入 %s（%d 处修改，原子替换）" % (os.path.relpath(tgt, ROOT), len(hits)))

    ok, msg = verify(tgt)
    log("校验：%s" % msg)
    if not ok:
        shutil.copy2(bak, tgt)
        log("校验失败，已自动回滚到备份")
        return 1
    log("补丁完成，文件保持可用；下一轮抓取生效（正在运行的那一轮仍用旧代码，属预期）")
    return 0


if __name__ == "__main__":
    sys.exit(main())