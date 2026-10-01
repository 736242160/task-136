#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""古诗格律校验工具（纯标准库，单文件）

输入 JSON 格式：
{
  "chars": {"中": "平", "月": "仄", ...},          # 字表：字 -> 平/仄
  "rhyme_groups": {"东": ["中", "风"], ...},        # 韵部表：韵部名 -> 字列表
  "poems": [
    {
      "id": "p1",
      "form": {"line_chars": 5, "lines": 4},        # 诗体要求：每句字数、句数（可缺省）
      "rhyme": "东",                                # 指定韵部（可缺省，缺省则由第2句推断）
      "lines": ["月中山火风", ...]                  # 句序列，字符串或字列表均可
    }
  ]
}

校验规则：
1. 押韵：偶数句（2、4、6...）末字必须同韵部。韵部状态跨句延续：
   若诗指定了韵部，则以之为基准；否则以第 2 句末字所属韵部为基准，
   后续每个押韵位置与基准取交集，交集为空即报"不押韵"，
   报错后基准不变（避免级联误报）。
2. 韵部表引用了字表中不存在的字 -> 报错（全局错误）。
3. 平仄：句内相邻字平仄必须交替，失替报错（句、位置、两字）。
4. 同字多韵部（两属）：只警告不判错。理由：传统韵书（如平水韵）
   本就有两属字（如"重"属东又属冬），判错会误杀合法作品；
   押韵判定用韵部交集，天然兼容两属。
5. 句字数、句数与诗体要求不一致 -> 报错。
6. 诗引用了不存在的韵部 -> 报错，并跳过该诗的押韵校验。
7. 诗中字不在字表 -> 报错（无法定平仄与韵）。

用法：
  python3 poem_check.py            # 运行内置自测样例
  python3 poem_check.py data.json  # 校验指定输入文件
"""

import json
import sys
from collections import defaultdict


# ---------- 错误收集 ----------

class Report:
    def __init__(self):
        self.global_errors = []   # 韵部表/字表层级错误
        self.warnings = []        # 两属等警告
        self.poems = []           # 每诗 {id, ok, errors}

    @property
    def ok(self):
        return (not self.global_errors
                and all(p["ok"] for p in self.poems))

    def to_dict(self):
        return {
            "ok": self.ok,
            "global_errors": self.global_errors,
            "warnings": self.warnings,
            "poems": self.poems,
            "summary": {
                "poem_count": len(self.poems),
                "bad_poem_count": sum(1 for p in self.poems if not p["ok"]),
                "error_count": (len(self.global_errors)
                                + sum(len(p["errors"]) for p in self.poems)),
                "warning_count": len(self.warnings),
            },
        }


def _err(kind, message, **kw):
    e = {"kind": kind, "message": message}
    e.update(kw)
    return e


# ---------- 韵部表装载 ----------

def load_rhyme_index(chars, rhyme_groups, report):
    """构建 字 -> 韵部集合 的索引；检查韵部引用的字是否都在字表中。"""
    char2groups = defaultdict(set)
    for group, words in rhyme_groups.items():
        for w in words:
            if w not in chars:
                report.global_errors.append(_err(
                    "UNKNOWN_CHAR_IN_RHYME_GROUP",
                    "韵部「%s」引用了字表中不存在的字「%s」" % (group, w),
                    group=group, char=w))
            char2groups[w].add(group)
    # 两属：警告而非错误（理由见模块 docstring 第 4 条）
    for w, groups in sorted(char2groups.items()):
        if len(groups) > 1:
            report.warnings.append(_err(
                "MULTI_RHYME_CHAR",
                "字「%s」同属多个韵部：%s（两属，按交集规则处理）"
                % (w, "、".join(sorted(groups))),
                char=w, groups=sorted(groups)))
    return char2groups


# ---------- 单诗校验 ----------

def check_poem(poem, chars, rhyme_groups, char2groups):
    errors = []
    pid = poem.get("id", "<未命名>")
    form = poem.get("form") or {}
    lines = [list(l) if not isinstance(l, str) else list(l)
             for l in poem.get("lines", [])]

    # 6. 诗引用不存在的韵部
    declared = poem.get("rhyme")
    rhyme_ok = True
    if declared is not None and declared not in rhyme_groups:
        errors.append(_err(
            "UNKNOWN_RHYME_GROUP",
            "诗「%s」引用了不存在的韵部「%s」" % (pid, declared),
            group=declared))
        rhyme_ok = False

    # 5a. 句数与诗体不符
    if form.get("lines") is not None and len(lines) != form["lines"]:
        errors.append(_err(
            "LINE_COUNT_MISMATCH",
            "诗「%s」句数 %d 与诗体要求 %d 不符" % (pid, len(lines), form["lines"]),
            expected=form["lines"], actual=len(lines)))

    # 5b/7/3. 逐句：字数、字表、平仄交替
    for i, line in enumerate(lines, 1):
        if form.get("line_chars") is not None and len(line) != form["line_chars"]:
            errors.append(_err(
                "LINE_LENGTH_MISMATCH",
                "诗「%s」第 %d 句字数 %d 与诗体要求 %d 不符"
                % (pid, i, len(line), form["line_chars"]),
                line=i, expected=form["line_chars"], actual=len(line)))
        tones = []
        for j, ch in enumerate(line, 1):
            if ch not in chars:
                errors.append(_err(
                    "UNKNOWN_CHAR",
                    "诗「%s」第 %d 句第 %d 字「%s」不在字表中，无法定平仄与韵"
                    % (pid, i, j, ch),
                    line=i, pos=j, char=ch))
                tones.append(None)
            else:
                tones.append(chars[ch])
        for j in range(len(line) - 1):
            t1, t2 = tones[j], tones[j + 1]
            if t1 and t2 and t1 == t2:
                errors.append(_err(
                    "TONE_ALTERNATION",
                    "诗「%s」第 %d 句第 %d-%d 字「%s%s」平仄失替（均为%s）"
                    % (pid, i, j + 1, j + 2, line[j], line[j + 1], t1),
                    line=i, pos=j + 1, chars=[line[j], line[j + 1]], tone=t1))

    # 1. 押韵：偶数句末字，韵部状态跨句延续
    if rhyme_ok:
        expected = {declared} if declared else None  # 当前基准韵部集合
        for i, line in enumerate(lines, 1):
            if i % 2 != 0 or not line:
                continue
            last = line[-1]
            groups = char2groups.get(last, set())
            if last not in chars:
                continue  # 字表错误已报，跳过
            if not groups:
                errors.append(_err(
                    "RHYME_MISMATCH",
                    "诗「%s」第 %d 句末字「%s」不属于任何韵部，无法押韵"
                    % (pid, i, last),
                    line=i, char=last, groups=[]))
                continue
            if expected is None:
                expected = set(groups)  # 第 2 句确立基准，向后延续
                continue
            inter = expected & groups
            if not inter:
                errors.append(_err(
                    "RHYME_MISMATCH",
                    "诗「%s」第 %d 句末字「%s」属韵部「%s」，与本诗韵部「%s」不押韵"
                    % (pid, i, last, "、".join(sorted(groups)),
                       "、".join(sorted(expected))),
                    line=i, char=last,
                    groups=sorted(groups), expected=sorted(expected)))
                # 基准不变：后续句仍按原韵部校验，避免级联误报
            else:
                expected = inter  # 两属字收窄基准，状态延续到下一句

    return {"id": pid, "ok": not errors, "errors": errors}


# ---------- 入口 ----------

def validate(data):
    report = Report()
    chars = data.get("chars", {})
    rhyme_groups = data.get("rhyme_groups", {})
    char2groups = load_rhyme_index(chars, rhyme_groups, report)
    for poem in data.get("poems", []):
        report.poems.append(check_poem(poem, chars, rhyme_groups, char2groups))
    return report


def print_report(report):
    d = report.to_dict()
    print("=" * 60)
    print("校验结果：%s" % ("全部通过" if d["ok"] else "存在错误"))
    s = d["summary"]
    print("诗数 %d，不合格 %d，错误 %d，警告 %d"
          % (s["poem_count"], s["bad_poem_count"],
             s["error_count"], s["warning_count"]))
    for e in d["global_errors"]:
        print("[全局错误][%s] %s" % (e["kind"], e["message"]))
    for w in d["warnings"]:
        print("[警告][%s] %s" % (w["kind"], w["message"]))
    for p in d["poems"]:
        print("-" * 60)
        print("诗「%s」：%s" % (p["id"], "通过" if p["ok"] else "不合格"))
        for e in p["errors"]:
            print("  [错误][%s] %s" % (e["kind"], e["message"]))
    print("=" * 60)
    print(json.dumps(d, ensure_ascii=False, indent=2))


# ---------- 自测样例 ----------

SELFTEST_DATA = {
    "chars": {
        "中": "平", "风": "平", "空": "平", "红": "平", "重": "平",
        "山": "平", "松": "平", "钟": "平", "高": "平", "秋": "平",
        "云": "平",
        "月": "仄", "日": "仄", "水": "仄", "火": "仄", "叶": "仄",
        "落": "仄", "梦": "仄", "远": "仄",
    },
    "rhyme_groups": {
        "东": ["中", "风", "空", "红", "重"],
        "冬": ["松", "钟", "重"],          # "重" 两属 -> 警告
        "微": ["飞", "归"],                # "飞""归" 不在字表 -> 全局错误
    },
    # 备用句（均严格平仄交替）：
    #   仄起: 月风水风远 / 日风水中月 / 落山火云梦
    #   平起: 山月风日中 / 高月山火中 / 秋月山水风 / 高水风日红
    #         松月山火松(冬韵) / 秋月山水重(两属)
    "poems": [
        {  # p1：完全合法的五绝（句内平仄严格交替，2/4 句押东韵）
            "id": "p1-合格",
            "form": {"line_chars": 5, "lines": 4},
            "rhyme": "东",
            "lines": ["月风水风远", "山月风日中", "日风水中月", "高月山火中"],
        },
        {  # p2：第 4 句末字"松"属冬韵，与东不押韵
            "id": "p2-不押韵",
            "form": {"line_chars": 5, "lines": 4},
            "rhyme": "东",
            "lines": ["月风水风远", "山月风日中", "日风水中月", "松月山火松"],
        },
        {  # p3：第 3 句 6 字，与五言不符；且句数 3 与绝句 4 句不符
            "id": "p3-字数句数不符",
            "form": {"line_chars": 5, "lines": 4},
            "rhyme": "东",
            "lines": ["月风水风远", "山月风日中", "日风水中月风"],
        },
        {  # p4：引用不存在的韵部"麻"
            "id": "p4-韵部不存在",
            "form": {"line_chars": 5, "lines": 4},
            "rhyme": "麻",
            "lines": ["月风水风远", "山月风日中", "日风水中月", "高月山火中"],
        },
        {  # p5：第 3 句"山云"平平失替
            "id": "p5-失替",
            "form": {"line_chars": 5, "lines": 4},
            "rhyme": "东",
            "lines": ["月风水风远", "山月风日中", "日山云水风", "秋月山水风"],
        },
        {  # p6：未指定韵部，由第 2 句推断为冬，第 4 句"中"属东 -> 不押韵
            "id": "p6-推断韵部后不押韵",
            "form": {"line_chars": 5, "lines": 4},
            "lines": ["月风水风远", "松月山火松", "日风水中月", "高月山火中"],
        },
        {  # p7：两属字"重"押韵：东韵基准下用"重"（东冬两属）应通过
            "id": "p7-两属字押韵",
            "form": {"line_chars": 5, "lines": 4},
            "rhyme": "东",
            "lines": ["月风水风远", "秋月山水重", "日风水中月", "高月山火中"],
        },
        {  # p8：含字表外字"花"
            "id": "p8-生字",
            "form": {"line_chars": 5, "lines": 4},
            "rhyme": "东",
            "lines": ["月风水风远", "山月风日中", "日风水中月", "高月山火花"],
        },
    ],
}

SELFTEST_EXPECT = {
    "p1-合格": [],
    "p2-不押韵": ["RHYME_MISMATCH"],
    "p3-字数句数不符": ["LINE_LENGTH_MISMATCH", "LINE_COUNT_MISMATCH"],
    "p4-韵部不存在": ["UNKNOWN_RHYME_GROUP"],
    "p5-失替": ["TONE_ALTERNATION"],
    "p6-推断韵部后不押韵": ["RHYME_MISMATCH"],
    "p7-两属字押韵": [],
    "p8-生字": ["UNKNOWN_CHAR"],
}


def selftest():
    report = validate(SELFTEST_DATA)
    print_report(report)
    failures = []
    by_id = {p["id"]: p for p in report.poems}
    for pid, want_kinds in SELFTEST_EXPECT.items():
        got = sorted(e["kind"] for e in by_id[pid]["errors"])
        if got != sorted(want_kinds):
            failures.append("诗 %s：期望 %s，实际 %s" % (pid, want_kinds, got))
    n_global = len(report.global_errors)
    if n_global != 2:  # 微韵部两个字不在字表
        failures.append("全局错误期望 2 条，实际 %d 条" % n_global)
    n_warn = len(report.warnings)
    if n_warn != 1:  # "重" 两属
        failures.append("两属警告期望 1 条，实际 %d 条" % n_warn)
    if failures:
        print("\n自测失败：")
        for f in failures:
            print("  - " + f)
        return 1
    print("\n自测全部通过（%d 首诗、%d 条全局错误、%d 条两属警告均符合预期）"
          % (len(by_id), n_global, n_warn))
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8") as f:
            data = json.load(f)
        print_report(validate(data))
    else:
        sys.exit(selftest())
