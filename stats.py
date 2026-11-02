#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stats.py — 分析记录质量审计与统计工具
================================================================================

【为什么有这个脚本】
    ANALYSIS-LOG.md 的记录质量依赖"自觉"，而自觉会随热情衰减。具体三个问题：

    1. 口算不可靠
       2026-10-30 曾口头统计"7 条记录中 5 条逆向、2 条顺向"，实际用 grep 核对是
       4 逆向 + 2 逆向偏中性 + 1 顺向。人脑数文本必然出错，统计必须交给程序。

    2. 格式会腐化
       设计脚本正则时发现 A005 写的是"对极端态组的警告为**逆向**"，其余记录都是
       "本判断为**逆向**"——措辞不一致会让统计静默漏项。若无校验，这类漂移会累积。

    3. 判断会被遗忘
       每条记录都有验证期限，但没人提醒就会永远停在"⏳待验证"，系统退化成日记。
       到期未回填必须被主动暴露。

【三块功能的动机】
    统计（默认）  ：算出真实分布，替代口算。
    校验（--check）：把"自觉"变成"强制"。缺五要素直接 exit 1，可挂 pre-commit。
    回写（--update）：顶部统计表由脚本维护，避免表与记录不一致。

【五要素（缺一即为不合格）】
    置信度 / 当时共识 / 方向性预测 / 验证哨 / 证伪条件
    理由见 ANALYSIS-LOG.md 使用规则第 2、4、5、6 条：
      无置信度 → 无法区分"模型缺陷"与"正常损耗"
      无共识   → 无法审计逆向成色（M01：好投资 = 成长 × 逆向）
      无预测   → 判断不可证伪，等于没写
      无哨位   → 到期只能事后讲故事
      无证伪   → 复盘必然滑向自我安慰

【迭代规则 — 每次修改必须在此登记】
    格式：YYYY-MM-DD | 改了什么 | 为什么改
    ------------------------------------------------------------------
    2026-10-30 | 首版：统计 + 校验 + 回写三模式 | 解决口算错误、格式腐化、验证遗忘
    2026-11-02 | 错误类型改为只从“错误类型：”字段提取 | 首版全文匹配 ERR_TYPES，把 A003/A006“错误预演”里的“时间错”字样误计为真实错误，导致 0 错记录报出“时间错 2”。统计必须只认专门字段，不能全文搜词。
    ------------------------------------------------------------------

    注意：修改统计口径（如"逆向偏中性"是否计入逆向）时，必须同时说明对历史数据的
    影响，否则跨期正确率不可比。

【用法】
    python3 stats.py                    打印统计
    python3 stats.py --check            只做格式校验，不合格 exit 1（可挂 pre-commit）
    python3 stats.py --update           回写顶部统计表到 ANALYSIS-LOG.md
    python3 stats.py --file <path>      指定记录文件（默认 ./ANALYSIS-LOG.md）
"""

import argparse
import re
import sys
from datetime import date, datetime
from pathlib import Path

DEFAULT_LOG = Path(__file__).resolve().parent / "ANALYSIS-LOG.md"

STATUS_ICONS = ["⏳", "✅", "❌", "⚪", "🚫"]
STATUS_NAMES = {"⏳": "待验证", "✅": "正确", "❌": "错误", "⚪": "部分正确", "🚫": "失效"}

ERR_TYPES = ["前提错", "逻辑错", "时间错", "运气错"]


def parse_records(text: str):
    """把 ANALYSIS-LOG.md 切分成记录块，解析字段。

    记录以 '## A001 | 标题' 开头。解析思路是"宽进严出"：
    先宽松匹配标题，再逐字段用正则提取；提取不到的字段记为 None，
    由 check 模式统一报错，避免解析器本身成为新的故障点。
    """
    # 只取正文章节，跳过文件头部的使用规则/统计表
    blocks = re.split(r"^## (A\d{3})\s*\|\s*(.+)$", text, flags=re.MULTILINE)
    records = []
    # blocks = [前言, id1, title1, body1, id2, title2, body2, ...]
    for i in range(1, len(blocks), 3):
        rid = blocks[i]
        title = blocks[i + 1].strip()
        body = blocks[i + 2] if i + 2 < len(blocks) else ""
        # 去掉下一个一级标题之后的内容（模型迭代记录等非记录章节）
        body = re.split(r"^---$", body, maxsplit=1)[0]

        status, deadline = None, None
        m = re.search(r"\*\*验证状态\*\*[：:]\s*([⏳✅❌⚪🚫]?)", body)
        if m:
            status = m.group(1) or None
        m = re.search(r"期限\s*(\d{4}-\d{2}-\d{2})", body)
        if m:
            deadline = m.group(1)

        direction = None
        m = re.search(r"(?:本判断为|警告为)\*\*(逆向偏中性|逆向|顺向)\*\*", body)
        if m:
            direction = m.group(1)

        confidence = None
        m = re.search(r"\*\*置信度\*\*[：:]\s*([^\n（(]+)", body)
        if m:
            confidence = m.group(1).strip()

        fields = {
            "置信度": "置信度" in body,
            "当时共识": "当时共识" in body,
            "方向性预测": "方向性预测" in body,
            "验证哨": "验证哨" in body,
            "证伪条件": "证伪条件" in body,
        }

        err_types = []
        # 只认专门的“错误类型：”字段，避免“错误预演”文本里的同名字样被误计
        m = re.search(r"错误类型[：:]\s*([^\n|]*)", body)
        if m:
            err_types = [e for e in ERR_TYPES if e in m.group(1)]

        records.append({
            "id": rid,
            "title": title,
            "status": status,
            "deadline": deadline,
            "direction": direction,
            "confidence": confidence,
            "fields": fields,
            "err_types": err_types,
        })
    return records


def days_left(deadline: str):
    try:
        d = datetime.strptime(deadline, "%Y-%m-%d").date()
        return (d - date.today()).days
    except Exception:
        return None


def print_stats(records):
    total = len(records)
    print("=" * 62)
    print(f"分析记录统计 | {date.today().isoformat()}")
    print("=" * 62)
    if not records:
        print("未找到任何记录（格式应为 '## A001 | 标题'）")
        return

    ids = [r["id"] for r in records]
    print(f"记录总数：{total}（{ids[0]}-{ids[-1]}，下一个编号 A{int(ids[-1][1:]) + 1:03d}）")
    print()

    # 验证状态
    counts = {k: 0 for k in STATUS_NAMES}
    unknown = 0
    for r in records:
        if r["status"] in counts:
            counts[r["status"]] += 1
        else:
            unknown += 1
    line = " | ".join(f"{k}{STATUS_NAMES[k]} {v}" for k, v in counts.items())
    print(f"验证状态：{line}" + (f" | 未标注 {unknown}" if unknown else ""))
    print()

    # 判断方向（逆向占比 = 明确逆向 + 逆向偏中性，口径见文件头迭代规则）
    dir_counts = {}
    for r in records:
        dir_counts[r["direction"] or "未标注"] = dir_counts.get(r["direction"] or "未标注", 0) + 1
    tagged = sum(v for k, v in dir_counts.items() if k != "未标注")
    contra = dir_counts.get("逆向", 0) + dir_counts.get("逆向偏中性", 0)
    dir_line = " | ".join(f"{k} {v}" for k, v in dir_counts.items())
    ratio = f"{contra / tagged * 100:.0f}%" if tagged else "—"
    print(f"判断方向：{dir_line}  →  逆向占比 {ratio}")
    print()

    # 置信度
    conf_counts = {}
    for r in records:
        conf_counts[r["confidence"] or "未标注"] = conf_counts.get(r["confidence"] or "未标注", 0) + 1
    print("置信度：  " + " | ".join(f"{k} {v}" for k, v in conf_counts.items()))
    print()

    # 错误类型
    err_counts = {e: sum(1 for r in records if e in r["err_types"]) for e in ERR_TYPES}
    print("错误类型：" + " | ".join(f"{k} {v}" for k, v in err_counts.items())
          + "（回填验证记录时标注）")
    print()

    # 到期提醒
    pending = [r for r in records if r["status"] == "⏳" and r["deadline"]]
    overdue = [r for r in pending if (days_left(r["deadline"]) or 0) < 0]
    if pending:
        nxt = min(pending, key=lambda r: r["deadline"])
        dl = days_left(nxt["deadline"])
        print(f"到期提醒：最近 {nxt['id']}（{nxt['deadline']}，剩余 {dl} 天）")
    else:
        print("到期提醒：无待验证记录")
    print(f"          已过期未回填：{len(overdue)} 条"
          + ("  ⚠️ " + ", ".join(r["id"] for r in overdue) if overdue else ""))
    print()


def print_check(records) -> bool:
    """校验五要素完整性。返回 True 表示全部通过。"""
    ok = True
    for r in records:
        missing = [k for k, v in r["fields"].items() if not v]
        if not r["status"]:
            missing.append("验证状态")
        if not r["deadline"]:
            missing.append("验证期限")
        if not r["direction"]:
            missing.append("判断方向（当时共识行需含'本判断为**逆向/顺向**'）")
        if missing:
            ok = False
            print(f"❌ {r['id']} {r['title']} 缺：{'、'.join(missing)}")
    if ok:
        print(f"✅ 格式校验通过（{len(records)}/{len(records)} 条五要素齐全）")
    else:
        print(f"\n共 {len(records)} 条记录，存在缺失项。请补齐后重试。")
    return ok


def update_table(path: Path, records):
    """回写顶部统计表。只替换'## 正确率统计'到下一个 '---' 之间的表格。"""
    text = path.read_text(encoding="utf-8")
    total = len(records)
    status_cnt = {}
    for r in records:
        status_cnt[r["status"]] = status_cnt.get(r["status"], 0) + 1

    done = total - status_cnt.get("⏳", 0)
    ok_cnt = status_cnt.get("✅", 0)
    neutral = status_cnt.get("⚪", 0)
    bad = status_cnt.get("❌", 0)
    void = status_cnt.get("🚫", 0)
    rate = f"{ok_cnt / done * 100:.0f}%" if done else "—"

    dir_cnt = {}
    for r in records:
        dir_cnt[r["direction"] or "未标注"] = dir_cnt.get(r["direction"] or "未标注", 0) + 1
    tagged = sum(v for k, v in dir_cnt.items() if k != "未标注")
    contra = dir_cnt.get("逆向", 0) + dir_cnt.get("逆向偏中性", 0)
    contra_rate = f"{contra / tagged * 100:.0f}%" if tagged else "—"

    errs = " / ".join(f"{e} {sum(1 for r in records if e in r['err_types'])}" for e in ERR_TYPES)

    table = (
        "| 指标 | 数值 |\n"
        "|:---|:---:|\n"
        f"| 已到期验证 | {done} |\n"
        f"| ✅ 跑赢基准的正确判断 | {ok_cnt} |\n"
        f"| ⚪ 对但跑输基准 / 路径不可承受 | {neutral} |\n"
        f"| ❌ 错误 | {bad} |\n"
        f"| 🚫 失效 | {void} |\n"
        f"| **相对正确率** | {rate} |\n"
        f"| 逆向判断占比 | {contra_rate} |\n"
        f"| 错误类型分布 | {errs} |\n"
    )

    new_text, n = re.subn(
        r"(## 正确率统计[^\n]*\n\n)(?:\|.*\n)+",
        lambda m: m.group(1) + table,
        text,
    )
    if n:
        path.write_text(new_text, encoding="utf-8")
        print(f"✅ 已回写统计表到 {path.name}（记录 {total} 条）")
    else:
        print("⚠️ 未找到'## 正确率统计'表格，跳过回写")


def main():
    ap = argparse.ArgumentParser(description="分析记录统计与质量审计")
    ap.add_argument("--check", action="store_true", help="只做格式校验，不合格 exit 1")
    ap.add_argument("--update", action="store_true", help="回写顶部统计表")
    ap.add_argument("--file", default=str(DEFAULT_LOG), help="记录文件路径")
    args = ap.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"❌ 找不到文件：{path}")
        sys.exit(2)

    records = parse_records(path.read_text(encoding="utf-8"))

    if args.check:
        sys.exit(0 if print_check(records) else 1)

    print_stats(records)
    print_check(records)

    if args.update:
        print()
        update_table(path, records)


if __name__ == "__main__":
    main()
