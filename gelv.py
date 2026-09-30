#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gelv.py — 古诗格律校验工具（纯 Python 标准库，单文件）

功能
====
输入一份 JSON，包含：
  zibu  字声表：{"春": "ping", "月": "ze", ...}           （平/仄的唯一来源）
  yunbu 韵部表：[{"name": "阳", "chars": ["光", "霜"]}]    （韵部 -> 字列表）
  poems 诗流：  [{"title": "...", "rhyme": "阳",
                  "form": {"line_length": 5, "line_count": 4},
                  "lines": [["春","月",...], ... 或 "春月花月春", ...]}]

校验规则
========
1. 押韵：偶数句（第 2、4、6…句）末字必须属于本诗的"有效韵部"，
   不押韵时报告句号、韵脚字、该字实际所属韵部。
2. 韵部引字不存在：韵部表中的字若不在字声表中，报错（表级）。
3. 平仄：句内相邻字平仄必须交替（严格交替律），失替报错；
   诗中用字不在字声表中也报错。
4. 一字两属：同一字出现在多个韵部时——
   【规则】取韵部表中最先定义的韵部为"正属"，其余为"通属"；
   押韵判定时只要该字的任一韵部与有效韵部相同即算押韵，
   同时在加载时给出 warning 清单。
   【理由】古诗中一字多读多义、两属通押是客观存在的现象
   （如"长"属阳韵又读上声养韵、"看"平仄两读），一律判错会误报；
   但两属也可能是编表笔误，故以 warning 形式保留提示，由人裁决。
5. 诗体：句字数须等于 form.line_length，句数须等于 form.line_count
   （两者均可选，缺省不校验）。
6. 诗引韵部不存在：poem.rhyme 指向未定义的韵部，报错。
7. 跨句状态延续：校验器在每首诗内维护"有效韵部"状态——
   若诗声明了 rhyme，则状态由声明初始化；否则在首个押韵位置
   （第 2 句末字）确立为其正属韵部，之后延续到所有后续偶数句。

用法
====
  python3 gelv.py 输入.json      # 校验文件（"-" 表示标准输入）
  python3 gelv.py --demo         # 用内置样例跑一遍并打印报告
  python3 gelv.py --selftest     # 运行自测断言
退出码：存在 error 级问题为 1，否则为 0。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from typing import Optional

PING = "ping"
ZE = "ze"
TONE_NAME = {PING: "平", ZE: "仄"}

TABLE_SCOPE = "<韵部表>"


@dataclass
class Issue:
    level: str               # "error" | "warning"
    kind: str                # 问题类别
    poem: str                # 诗题；表级问题为 TABLE_SCOPE
    line: Optional[int]      # 句号（1 起），无则 None
    char: Optional[str]      # 相关字，无则 None
    message: str

    def fmt(self) -> str:
        loc = self.poem
        if self.line is not None:
            loc += f" 第{self.line}句"
        if self.char:
            loc += f"「{self.char}」"
        return f"[{self.level:7s}] {self.kind} @ {loc}：{self.message}"


class Checker:
    """格律校验器：先加载字声表与韵部表，再逐诗校验。"""

    def __init__(self, data: dict):
        self.zibu: dict = data.get("zibu", {})
        self.issues: list[Issue] = []
        self.yunbu: dict[str, list[str]] = {}      # 韵部名 -> 字列表
        self.char2yuns: dict[str, list[str]] = {}  # 字 -> [韵部名]（按定义顺序，首为正属）
        self._load_yunbu(data.get("yunbu", []))

    # ---- 表级加载与校验 -------------------------------------------------

    def _load_yunbu(self, entries: list) -> None:
        for entry in entries:
            name = entry.get("name")
            if name in self.yunbu:
                self.issues.append(Issue(
                    "error", "韵部重复", TABLE_SCOPE, None, None,
                    f"韵部「{name}」重复定义，后者被忽略"))
                continue
            self.yunbu[name] = list(entry.get("chars", []))

        for name, chars in self.yunbu.items():
            for ch in chars:
                if ch not in self.zibu:
                    self.issues.append(Issue(
                        "error", "韵部引字不存在", TABLE_SCOPE, None, ch,
                        f"韵部「{name}」中的字不在字声表里"))
                self.char2yuns.setdefault(ch, []).append(name)

        for ch, names in self.char2yuns.items():
            if len(names) > 1:
                self.issues.append(Issue(
                    "warning", "一字两属", TABLE_SCOPE, None, ch,
                    f"该字同属韵部 {names}，取「{names[0]}」为正属，"
                    f"押韵时任一属匹配即算通过"))

    # ---- 诗级校验 -------------------------------------------------------

    def check_poem(self, poem: dict) -> bool:
        title = poem.get("title", "<无题>")
        lines = [list(l) if isinstance(l, str) else list(l)
                 for l in poem.get("lines", [])]
        form = poem.get("form", {}) or {}
        start = len(self.issues)

        # 诗体：句数
        line_count = form.get("line_count")
        if line_count is not None and len(lines) != line_count:
            self.issues.append(Issue(
                "error", "句数不符", title, None, None,
                f"诗体要求 {line_count} 句，实际 {len(lines)} 句"))

        # 诗引韵部：初始化跨句状态
        declared = poem.get("rhyme")
        state_rhyme: Optional[str] = None
        if declared is not None:
            if declared not in self.yunbu:
                self.issues.append(Issue(
                    "error", "诗引韵部不存在", title, None, None,
                    f"诗声明的韵部「{declared}」在韵部表中没有定义，"
                    f"押韵状态将改为由首个押韵句推断"))
            else:
                state_rhyme = declared

        line_length = form.get("line_length")
        for idx, chars in enumerate(lines, 1):
            # 诗体：句字数
            if line_length is not None and len(chars) != line_length:
                self.issues.append(Issue(
                    "error", "句字数不符", title, idx, None,
                    f"诗体要求每句 {line_length} 字，实际 {len(chars)} 字"))
            self._check_pingze(title, idx, chars)
            # 押韵位置：偶数句末字；状态跨句延续
            if idx % 2 == 0 and chars:
                state_rhyme = self._check_rhyme(title, idx, chars[-1],
                                                state_rhyme)

        return not any(i.level == "error" for i in self.issues[start:])

    def _check_pingze(self, title: str, idx: int, chars: list) -> None:
        tones = []
        for ch in chars:
            if ch not in self.zibu:
                self.issues.append(Issue(
                    "error", "字表缺字", title, idx, ch,
                    "诗中用字不在字声表里，无法判定平仄"))
                tones.append(None)
            else:
                tones.append(self.zibu[ch])
        for pos in range(len(tones) - 1):
            a, b = tones[pos], tones[pos + 1]
            if a is not None and a == b:
                self.issues.append(Issue(
                    "error", "平仄失替", title, idx, chars[pos + 1],
                    f"第{pos + 1}字「{chars[pos]}」与第{pos + 2}字"
                    f"「{chars[pos + 1]}」同为{TONE_NAME[a]}声，"
                    f"句内平仄未交替"))

    def _check_rhyme(self, title: str, idx: int, ch: str,
                     state_rhyme: Optional[str]) -> Optional[str]:
        groups = self.char2yuns.get(ch, [])
        if not groups:
            self.issues.append(Issue(
                "error", "韵脚无韵部", title, idx, ch,
                "押韵位置的末字不属于任何韵部"))
            return state_rhyme
        if state_rhyme is None:
            # 跨句状态在此确立：取正属韵部，延续到后续偶数句
            return groups[0]
        if state_rhyme not in groups:
            self.issues.append(Issue(
                "error", "不押韵", title, idx, ch,
                f"该字所属韵部为 {groups}，与本诗有效韵部"
                f"「{state_rhyme}」不一致"))
        return state_rhyme


# ---- 报告 ---------------------------------------------------------------

def print_report(results: list, issues: list) -> None:
    print("=== 校验结果 ===")
    for title, ok in results:
        print(f"  {'通过' if ok else '未通过'}  {title}")
    print()
    if issues:
        print("=== 错误 / 警告清单 ===")
        for it in issues:
            print("  " + it.fmt())
    else:
        print("未发现任何问题。")
    errors = sum(1 for i in issues if i.level == "error")
    warns = sum(1 for i in issues if i.level == "warning")
    print(f"\n合计：{errors} 个错误，{warns} 个警告；"
          f"{sum(1 for _, ok in results if ok)}/{len(results)} 首诗通过。")


def run(data: dict) -> int:
    checker = Checker(data)
    results = [(p.get("title", "<无题>"), checker.check_poem(p))
               for p in data.get("poems", [])]
    print_report(results, checker.issues)
    return 1 if any(i.level == "error" for i in checker.issues) else 0


# ---- 内置样例 -----------------------------------------------------------

def demo_data() -> dict:
    zibu = {}
    for ch in "春风花香山云光霜乡阳长红空中宫":
        zibu[ch] = PING
    for ch in "月夜雨梦水雁是地上望故落客照":
        zibu[ch] = ZE
    return {
        "zibu": zibu,
        "yunbu": [
            # “璋”故意不在字声表：演示“韵部引字不存在”
            {"name": "阳", "chars": list("光霜乡阳香长") + ["璋"]},
            # “长”两属：演示 warning 与通属规则
            {"name": "东", "chars": list("风红空中宫") + ["长"]},
        ],
        "poems": [
            {  # 全部合法：五言四句，偶句押阳韵，句内平仄严格交替
                "title": "春晓曲（合格）",
                "rhyme": "阳",
                "form": {"line_length": 5, "line_count": 4},
                "lines": ["春月花月春", "花雨花月香",
                          "月风夜花梦", "花月花月光"],
            },
            {  # 第2句押错韵（风属东）、第3句少一字、第4句平仄失替
                "title": "错题集（多种错误）",
                "rhyme": "阳",
                "form": {"line_length": 5, "line_count": 4},
                "lines": ["春月花月春", "花雨花月风",
                          "月风夜花", "月月花月光"],
            },
            {  # 未声明韵部：第2句末字“风”确立东韵并延续，第4句“光”出韵
                "title": "无题（韵部由状态推断）",
                "form": {"line_length": 5, "line_count": 4},
                "lines": ["春月花月春", "花雨花月风",
                          "月风夜花梦", "花月花月光"],
            },
            {  # 声明了不存在的韵部“寒”；且用了字声表没有的“龘”
                "title": "无韵（引用错误）",
                "rhyme": "寒",
                "form": {"line_length": 5, "line_count": 2},
                "lines": ["春月龘月春", "花雨花月香"],
            },
        ],
    }


# ---- 自测 ---------------------------------------------------------------

def selftest() -> int:
    checker = Checker(demo_data())
    results = {p["title"]: checker.check_poem(p) for p in demo_data()["poems"]}

    def kinds(title):
        return [i.kind for i in checker.issues if i.poem == title]

    def table_kinds():
        return [i.kind for i in checker.issues if i.poem == TABLE_SCOPE]

    # 合格诗必须零错误通过
    assert results["春晓曲（合格）"] is True, kinds("春晓曲（合格）")
    # 错题集：不押韵（报告所属韵部）、句字数不符、平仄失替
    bad = kinds("错题集（多种错误）")
    assert results["错题集（多种错误）"] is False
    assert bad.count("不押韵") == 1 and bad.count("句字数不符") == 1 \
        and bad.count("平仄失替") == 1, bad
    rhyme_issue = next(i for i in checker.issues if i.kind == "不押韵"
                       and i.poem == "错题集（多种错误）")
    assert rhyme_issue.line == 2 and rhyme_issue.char == "风" \
        and "东" in rhyme_issue.message, rhyme_issue.fmt()
    # 跨句状态延续：推断出东韵后，第4句“光”被判出韵
    infer = kinds("无题（韵部由状态推断）")
    assert infer == ["不押韵"], infer
    infer_issue = next(i for i in checker.issues
                       if i.poem == "无题（韵部由状态推断）")
    assert infer_issue.line == 4 and infer_issue.char == "光"
    # 诗引不存在的韵部 + 字表缺字
    badref = kinds("无韵（引用错误）")
    assert "诗引韵部不存在" in badref and "字表缺字" in badref, badref
    # 表级：韵部引字不存在（璋）、一字两属（长，warning）
    assert "韵部引字不存在" in table_kinds()
    two = next(i for i in checker.issues if i.kind == "一字两属")
    assert two.level == "warning" and two.char == "长"
    # 两属通押：长在东韵诗中作韵脚应判通过
    data = demo_data()
    data["poems"] = [{"title": "两属通押", "rhyme": "东",
                      "form": {"line_length": 5},
                      "lines": ["春月花月春", "花雨花月长"]}]
    c2 = Checker(data)
    assert c2.check_poem(data["poems"][0]) is True
    print("selftest OK：全部断言通过。")
    return 0


# ---- 入口 ---------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="古诗格律校验工具")
    ap.add_argument("file", nargs="?", help="输入 JSON 文件，'-' 表示标准输入")
    ap.add_argument("--demo", action="store_true", help="运行内置样例")
    ap.add_argument("--selftest", action="store_true", help="运行自测断言")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if args.demo:
        return run(demo_data())
    if not args.file:
        ap.error("请提供输入 JSON 文件，或使用 --demo / --selftest")
    text = sys.stdin.read() if args.file == "-" else open(
        args.file, encoding="utf-8").read()
    return run(json.loads(text))


if __name__ == "__main__":
    sys.exit(main())
