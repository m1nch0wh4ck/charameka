# -*- coding: utf-8 -*-
"""
MELLOY 캐릭터 검수기 v0.1

캐릭터 '원본 설정' 파일과 '생성 결과' 파일을 비교해서 검수하는 데스크톱 프로그램입니다.
  - 1단계 기계 검사 : 글자 수 제한, {{user}} 금지 위치, 필수 섹션/헤더 누락을 즉시 계산합니다.
  - 2단계 AI 의미 검수 : Claude Code CLI(claude -p)를 백그라운드로 호출해서
                         누락 / 금지 규칙 약화(기계어치환 원칙 위반) / 환각(없는 설정 추가)을 찾습니다.

  - 작성 탭 : rules/템플릿.md 의 ## 제목 단위로 칸을 나눠 자연어로 채우면, 템플릿 양식의 변환 파일을 만듭니다.
               (AI 없이 동작하며, 필요한 칸만 '이 칸 AI로 다듬기'로 Claude 에게 맡길 수 있습니다.)

보안을 위해 모든 경로는 '현재 실행 위치' 기준 상대경로로만 다룹니다.
(실행하기.bat 이 이 파일이 있는 폴더로 이동한 뒤 실행합니다.)
"""

from __future__ import annotations

import html
import json
import math
import os
import re
import shutil
import subprocess
import sys
import traceback
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QElapsedTimer, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QTreeWidgetItemIterator,
    QVBoxLayout,
    QWidget,
)

APP_TITLE = "MELLOY 캐릭터 검수기 v0.1"

# ── 상대경로 (현재 실행 위치 기준) ─────────────────────────────
RULES_DIR = Path("rules")  # 1인 모드 규칙 (기존 위치)
MODE_DIRS = {"1인": RULES_DIR, "다인": RULES_DIR / "다인"}  # 모드별 규칙 폴더
COMMON_DIR = RULES_DIR / "공통"  # 두 모드가 함께 쓰는 문서 (예: 플랫폼 문체지침)
MODE_FILE = Path("작업모드.txt")  # 마지막으로 고른 모드
_rule_mode = "1인"


def available_modes() -> list[str]:
    return [m for m, d in MODE_DIRS.items() if m == "1인" or (d / "템플릿.md").exists()]


def set_rule_mode(mode: str):
    global _rule_mode
    _rule_mode = mode if mode in available_modes() else "1인"


def rule_mode() -> str:
    return _rule_mode


def rules_dir() -> Path:
    return MODE_DIRS[_rule_mode]


def config_file() -> Path:
    return rules_dir() / "검수설정.json"
RESULT_DIR = Path("검수결과")

# ── 검수 결과 상태 ────────────────────────────────────────────
PASS, WARN, FAIL, INFO = "pass", "warn", "fail", "info"
STATUS_STYLE = {
    PASS: ("✔", "#1a7f37"),
    WARN: ("▲", "#b7791f"),
    FAIL: ("❌", "#c62828"),
    INFO: ("ℹ", "#5f6b7a"),
}

DEFAULT_CONFIG = {
    "_안내": "이 파일은 메모장으로 열어서 숫자나 이름만 바꾸면 됩니다. 따옴표와 쉼표는 지우지 마세요.",
    "글자수_제한": [
        {"필드": "등장인물", "별칭": ["등장인물", "캐릭터", "인물", "characters"], "최대": 5000},
        {"필드": "설명", "별칭": ["설명", "description"], "최대": 10000},
    ],
    "권장_비율": 2 / 3,
    "공백_포함_글자수": True,
    "필수_섹션": ["등장인물", "설명"],
    "템플릿_파일": "템플릿.md",
    "템플릿_헤더_필수": True,
    "원본_헤더_비교": True,
    "user_금지_섹션": ["이름"],
    "user_헤더_금지": True,
    "user_중첩_금지": True,
    "AI_제한시간_초": 600,
}


@dataclass
class CheckItem:
    status: str
    title: str
    detail: str = ""


@dataclass
class Section:
    name: str
    level: int
    body: str
    start_line: int | None = None  # 본문 시작 줄 (1부터), JSON 은 None
    end_line: int | None = None  # 본문 끝 줄 (포함)


# ════════════════════════════════════════════════════════════
#  공통 도구
# ════════════════════════════════════════════════════════════
def to_relative(path: str) -> str:
    """화면에 보여줄 경로를 현재 실행 위치 기준 상대경로로 바꿉니다."""
    try:
        return os.path.relpath(path, Path.cwd())
    except ValueError:  # 다른 드라이브 등 상대경로로 만들 수 없을 때는 파일 이름만
        return Path(path).name


def read_text_file(path: str | Path) -> str:
    """메모장/VS Code 에서 저장한 여러 인코딩을 차례대로 시도해서 읽습니다."""
    data = Path(path).read_bytes()
    for enc in ("utf-8-sig", "cp949", "utf-16"):
        try:
            return data.decode(enc)
        except UnicodeError:
            continue
    return data.decode("utf-8", errors="replace")


def load_config() -> tuple[dict, str | None]:
    """지금 모드의 검수설정.json 을 읽습니다. 1인 모드에서 없으면 기본값으로 만들어 둡니다."""
    config = dict(DEFAULT_CONFIG)
    path = config_file()
    try:
        if not path.exists():
            if rule_mode() != "1인":
                return config, f"설정 파일({path})이 없어 기본값을 사용합니다."
            RULES_DIR.mkdir(exist_ok=True)
            path.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
            return config, None
        loaded = json.loads(read_text_file(path))
        if isinstance(loaded, dict):
            config.update(loaded)
        return config, None
    except Exception as e:  # 설정 파일이 깨져도 기본값으로 계속 동작
        return config, f"설정 파일({path})을 읽지 못해 기본값을 사용합니다: {e}"


def normalize(name: str) -> str:
    return re.sub(r"[\s_\-:：·.()\[\]【】]", "", name).lower()


def line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def fmt_lines(lines: list[int]) -> str:
    uniq = sorted(set(lines))
    shown = ", ".join(str(n) for n in uniq[:15])
    return f"{shown}번째 줄" + (" 외" if len(uniq) > 15 else "")


# ════════════════════════════════════════════════════════════
#  섹션(헤더) 분석
# ════════════════════════════════════════════════════════════
MD_HEADER = re.compile(r"^\s{0,3}(#{1,6})\s*(.+?)\s*#*\s*$")


def parse_sections(text: str) -> list[Section]:
    """마크다운 제목(# 제목)으로 섹션을 나눕니다.
    [변수 이름], [한서윤 CHARACTER PROFILE] 같은 대괄호 줄은 본문으로 봅니다.
    JSON 파일이면 최상위 문자열 항목을 각각 섹션으로 봅니다."""
    stripped = text.lstrip()
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
            if isinstance(data, dict):
                return _sections_from_json(data, 1)
        except json.JSONDecodeError:
            pass
    return parse_md_sections(text)


def parse_md_sections(text: str) -> list[Section]:
    """'# 제목' 형태의 마크다운 헤더만으로 섹션을 나눕니다.
    본문 안의 [구간별 행동] 같은 대괄호 줄은 헤더로 보지 않습니다."""
    lines = text.splitlines()
    headers = [(i, len(m.group(1)), m.group(2).strip())
               for i, m in ((i, MD_HEADER.match(line)) for i, line in enumerate(lines)) if m]
    sections = []
    for n, (idx, level, name) in enumerate(headers):
        end = next((i2 for i2, lv2, _ in headers[n + 1:] if lv2 <= level), len(lines))
        sections.append(Section(name, level, "\n".join(lines[idx + 1:end]), idx + 2, end))
    return sections


def _sections_from_json(data: dict, level: int) -> list[Section]:
    result = []
    for key, value in data.items():
        if isinstance(value, str):
            result.append(Section(str(key), level, value))
        elif isinstance(value, list) and all(isinstance(v, str) for v in value):
            result.append(Section(str(key), level, "\n".join(value)))
        elif isinstance(value, dict):
            result.append(Section(str(key), level, json.dumps(value, ensure_ascii=False)))
            result.extend(_sections_from_json(value, level + 1))
    return result


def find_section(sections: list[Section], aliases: list[str]) -> Section | None:
    keys = [normalize(a) for a in aliases if a]
    for s in sections:  # 1순위: 이름이 정확히 같은 섹션
        if normalize(s.name) in keys:
            return s
    for s in sections:  # 2순위: 이름에 별칭이 들어있는 섹션 (예: '등장인물 필드')
        if any(k and k in normalize(s.name) for k in keys):
            return s
    return None


def ulen(text: str) -> int:
    """MELLOY·루모 화면과 같은 방식(자바스크립트 문자열 길이, UTF-16)으로 셉니다.
    이모지는 대부분 2자, 변이 선택자가 붙으면 3자입니다."""
    return len(text.encode("utf-16-le")) // 2


def count_chars(text: str, include_spaces: bool) -> int:
    text = text.strip()
    return ulen(text) if include_spaces else ulen(re.sub(r"\s", "", text))


def recommended_line(rule: dict, config: dict) -> tuple[int, str]:
    """칸별 권장선. 칸에 '권장'이나 '목표'가 있으면 그 값, 없으면 최대 × 권장_비율(기본 2/3).
    돌려주는 값: (권장선, 이름표 '권장' 또는 '목표')"""
    for key in ("권장", "목표"):
        v = int(rule.get(key, 0) or 0)
        if v:
            return v, key
    ratio = float(config.get("권장_비율", 2 / 3))
    return int(int(rule.get("최대", 0)) * ratio), "권장"


# ════════════════════════════════════════════════════════════
#  1단계: 기계 검사
# ════════════════════════════════════════════════════════════
USER_ANY = re.compile(r"\{\{\s*user\s*\}\}", re.IGNORECASE)
USER_SINGLE = re.compile(r"(?<!\{)\{\s*user\s*\}(?!\})", re.IGNORECASE)
USER_KOREAN = re.compile(r"\{\{\s*(유저|사용자)\s*\}\}")


def run_mechanical_checks(original: str, result: str, config: dict) -> list[tuple[str, list[CheckItem]]]:
    """결과를 [(그룹 제목, [검사 항목...]), ...] 형태로 돌려줍니다."""
    if not result.strip():
        return [("기본 확인", [CheckItem(FAIL, "생성 결과 내용이 비어 있습니다", "검사할 생성 결과 파일을 먼저 불러와 주세요.")])]

    res_sections = parse_sections(result)
    orig_sections = parse_sections(original) if original.strip() else []
    groups = [
        ("글자 수 검사", _check_lengths(res_sections, config)),
        ("{{user}} 위치 검사", _check_user_variable(result, res_sections, config)),
        ("필수 섹션 / 헤더 검사", _check_sections(res_sections, orig_sections, config)),
    ]
    # 아래 두 검사는 검수설정.json 에 해당 항목이 있을 때만 실행합니다.
    md_sections = parse_md_sections(result)
    if isinstance(config.get("합계_제한"), dict):
        groups.append(("설정 크기 검사", _check_total_length(md_sections, config)))
    if isinstance(config.get("변수_검사"), dict):
        groups.append(("변수 검사", _check_variables(md_sections, config)))
    if config.get("카드_항목"):
        groups.append(("등장인물 카드 검사", _check_cards(md_sections, config)))
    if isinstance(config.get("로어북_검사"), dict):
        groups.append(("로어북 검사", _check_lorebook(md_sections, config)))
    if config.get("턴_시간표현_금지"):
        groups.append(("시간 표현 검사", _check_turn_time(result)))
    return groups


def _check_lengths(sections: list[Section], config: dict) -> list[CheckItem]:
    items = []
    with_spaces = bool(config.get("공백_포함_글자수", True))
    basis = "공백 포함" if with_spaces else "공백 제외"
    for rule in config.get("글자수_제한", []):
        field = rule.get("필드", "?")
        limit = int(rule.get("최대", 0))
        sec = find_section(sections, [field] + list(rule.get("별칭", [])))
        if sec is None:
            items.append(CheckItem(WARN, f"'{field}' 필드를 찾지 못했습니다",
                                   f"헤더 이름을 확인해 주세요. (예: # {field})"))
            continue
        n = count_chars(sec.body, with_spaces)
        where = f" · {sec.start_line - 1}번째 줄의 '{sec.name}'" if sec.start_line else f" · '{sec.name}' 항목"
        rec, rec_name = recommended_line(rule, config)
        msg = f"{n:,}자 / {rec_name} {rec:,}자 · 최대 {limit:,}자 ({basis}){where}"
        if n > limit:
            items.append(CheckItem(FAIL, f"'{field}' 필드 글자 수 초과 (+{n - limit:,}자)", msg))
        elif n > rec:
            items.append(CheckItem(WARN, f"'{field}' 필드가 {rec_name}선을 넘었습니다 ({rec_name} {rec:,}자, +{n - rec:,}자)", msg))
        else:
            items.append(CheckItem(PASS, f"'{field}' 필드 글자 수 정상", msg))
    return items


@dataclass
class BraceMatch:
    tokens: list[tuple[int, str]]  # (위치, '{{' 또는 '}}')
    matched: set[int]  # 짝이 맞는 '{{' 의 위치
    unclosed: list[int]  # 끝까지 닫히지 않은 '{{' 의 위치
    stray: list[int]  # 여는 짝이 없는 '}}' 의 위치


def _match_braces(text: str) -> BraceMatch:
    """'{{' 와 '}}' 를 안쪽부터 짝지어, 짝이 없는 괄호의 위치를 찾습니다."""
    tokens = [(m.start(), m.group(0)) for m in re.finditer(r"\{\{|\}\}", text)]
    stack: list[int] = []
    matched: set[int] = set()
    stray: list[int] = []
    for pos, tok in tokens:
        if tok == "{{":
            stack.append(pos)
        elif stack:
            matched.add(stack.pop())
        else:
            stray.append(pos)
    return BraceMatch(tokens, matched, stack, stray)


def _line_preview(text: str, pos: int, before: bool = False) -> str:
    """'N번째 줄 — 미리보기' 형태로 보여 줍니다.
    before=False 면 해당 위치부터 20자, True 면 해당 괄호('}}')까지 앞쪽 20자를 보여 줍니다."""
    if before:
        start = text.rfind("\n", 0, pos) + 1
        snippet = text[start:pos + 2].strip()
        if len(snippet) > 20:
            snippet = "..." + snippet[-20:]
    else:
        end = text.find("\n", pos)
        snippet = text[pos:end if end >= 0 else len(text)].strip()
        if len(snippet) > 20:
            snippet = snippet[:20] + "..."
    return f"{line_of(text, pos)}번째 줄 — {snippet}"


def _check_user_variable(text: str, sections: list[Section], config: dict) -> list[CheckItem]:
    items = []
    exact = [m for m in USER_ANY.finditer(text) if m.group(0) == "{{user}}"]
    variants = [m for m in USER_ANY.finditer(text) if m.group(0) != "{{user}}"]
    problems = 0

    braces = _match_braces(text)

    # 1) 다른 변수 {{ ... }} 안에 {{user}}가 중첩된 경우
    #    짝이 맞는(닫히는) {{ 안에 있을 때만 중첩으로 봅니다.
    #    닫히지 않은 {{ 뒤에 있을 뿐인 {{user}}는 5)의 괄호 짝 경고로만 알려 줍니다.
    if config.get("user_중첩_금지", True):
        user_starts = {m.start() for m in exact + variants}
        nested_lines = []
        stack: list[int] = []
        for pos, tok in braces.tokens:
            if tok == "{{":
                if pos in user_starts and any(p in braces.matched for p in stack):
                    nested_lines.append(line_of(text, pos))
                stack.append(pos)
            elif stack:
                stack.pop()
        if nested_lines:
            problems += 1
            items.append(CheckItem(FAIL, "다른 변수 안에 {{user}}가 들어가 있습니다",
                                   f"{fmt_lines(nested_lines)} — 변수 안에 변수를 넣으면 치환이 깨집니다."))

    # 2) 헤더(제목) 줄 안의 {{user}}
    if config.get("user_헤더_금지", True):
        header_lines = [i + 1 for i, line in enumerate(text.splitlines())
                        if MD_HEADER.match(line) and USER_ANY.search(line)]
        if header_lines:
            problems += 1
            items.append(CheckItem(FAIL, "섹션 제목(헤더)에 {{user}}가 들어가 있습니다", fmt_lines(header_lines)))

    # 3) 금지 섹션 본문 안의 {{user}}
    for forbidden in config.get("user_금지_섹션", []):
        sec = find_section(sections, [forbidden])
        if sec is None or not USER_ANY.search(sec.body):
            continue
        problems += 1
        if sec.start_line:
            lines = [line_of(text, m.start()) for m in USER_ANY.finditer(text)
                     if sec.start_line <= line_of(text, m.start()) <= sec.end_line]
            detail = f"'{sec.name}' 섹션 · {fmt_lines(lines)}"
        else:
            detail = f"'{sec.name}' 항목"
        items.append(CheckItem(FAIL, f"'{forbidden}' 섹션에는 {{{{user}}}}를 쓸 수 없습니다", detail))

    # 4) 표기 실수 의심 ({{User}}, {{ user }}, {user}, {{유저}})
    typo_lines = [line_of(text, m.start()) for m in variants]
    typo_lines += [line_of(text, m.start()) for m in USER_SINGLE.finditer(text)]
    typo_lines += [line_of(text, m.start()) for m in USER_KOREAN.finditer(text)]
    if typo_lines:
        items.append(CheckItem(WARN, "{{user}} 표기가 정확하지 않은 곳이 있습니다",
                               f"{fmt_lines(typo_lines)} — 정확히 {{{{user}}}} (소문자, 공백 없음)로 써 주세요."))

    # 5) 중괄호 짝: 닫히지 않은 {{ 와 여는 짝이 없는 }} 의 위치를 알려 줍니다.
    if braces.unclosed or braces.stray:
        opens = sum(1 for _, tok in braces.tokens if tok == "{{")
        lines = [f"'{{{{' {opens}개, '}}}}' {len(braces.tokens) - opens}개"]
        lines += [f"닫히지 않은 '{{{{': {_line_preview(text, p)}" for p in braces.unclosed[:10]]
        lines += [f"여는 짝이 없는 '}}}}': {_line_preview(text, p, before=True)}" for p in braces.stray[:10]]
        extra = max(0, len(braces.unclosed) - 10) + max(0, len(braces.stray) - 10)
        if extra:
            lines.append(f"... 외 {extra}곳")
        items.append(CheckItem(WARN, "변수 괄호 짝이 맞지 않습니다", "\n".join(lines)))

    if problems == 0:
        items.insert(0, CheckItem(PASS, "금지된 위치에 {{user}}가 없습니다", f"{{{{user}}}} 사용 횟수: 총 {len(exact)}회"))
    return items


def _field_aliases(field: str, config: dict) -> list[str]:
    """글자수_제한에 같은 필드가 있으면 그 별칭도 함께 씁니다."""
    for rule in config.get("글자수_제한", []):
        if normalize(rule.get("필드", "")) == normalize(field):
            return [field] + list(rule.get("별칭", []))
    return [field]


def _matching_sections(sections: list[Section], aliases: list[str]) -> list[Section]:
    """가장 바깥 단계 헤더 중 이름이 별칭으로 시작하는 섹션을 모두 찾습니다.
    (예: '# 등장인물 2', '# 등장인물 - 한도윤')
    '# 로어북' 안의 '## 캐릭터 설정' 같은 하위 섹션은 세지 않습니다."""
    if not sections:
        return []
    top = min(s.level for s in sections)
    keys = [normalize(a) for a in aliases if a]
    return [s for s in sections
            if s.level == top and any(normalize(s.name).startswith(k) for k in keys)]


def _fmt_tokens(n: float) -> str:
    """MELLOY 표시처럼 1.3만 / 8.5천 꼴로 반올림합니다."""
    if n >= 10000:
        return f"{math.floor(n / 1000 + 0.5) / 10:.1f}만"
    return f"{math.floor(n / 100 + 0.5) / 10:.1f}천"


def token_text(total: int, config: dict) -> str:
    rate = config.get("토큰_환산")
    if not isinstance(rate, dict) or total <= 0:
        return ""
    lo, hi = total * float(rate.get("최소", 0.65)), total * float(rate.get("최대", 1.05))
    a, b = _fmt_tokens(lo), _fmt_tokens(hi)
    if a[-1] == b[-1]:
        a = a[:-1]
    return f"약 {a}~{b} 토큰"


@dataclass
class SizePart:
    field: str  # 설정 이름 (예: 등장인물)
    label: str  # 보여 줄 이름 (예: 주연 3명)
    used: int  # 설정 크기에 반영된 글자 수
    actual: int  # 실제 글자 수
    counts: list[int]  # 카드·섹션별 글자 수
    found: bool
    note: str = ""


def setting_size(sections: list[Section], config: dict) -> tuple[list[SizePart], int]:
    """MELLOY 설정 크기: 세계관(설명) / 주연 / 조연(턴당 최대 인원만) / 로어북(상한까지)"""
    rule = config["합계_제한"]
    lore_cap = int(rule.get("로어북_반영_상한", 3000))
    names = config.get("표시_이름") or {}
    parts, total = [], 0
    for field in rule.get("대상_필드", []):
        key = normalize(field)
        is_main = bool(config.get("카드_항목")) and key == normalize(config["카드_항목"])
        is_sub = bool(config.get("조연_항목")) and key == normalize(config["조연_항목"])
        label = names.get(field, field)
        note = ""
        if is_main or is_sub:  # 다인챗: 카드 제목 줄은 빼고 카드 본문만 더합니다
            head, cards = _card_sections(sections, config, field)
            found = head is not None
            counts = [count_chars(c.body, True) for c in cards]
            actual = sum(counts)
            label += f" {len(counts)}명"
            if is_sub:
                per_turn = int(config.get("조연_턴당_최대", 2))
                used = sum(sorted(counts, reverse=True)[:per_turn])
                note = f"턴당 최대 {per_turn}명"
            else:
                used = actual
        else:
            secs = _matching_sections(sections, _field_aliases(field, config))
            found = bool(secs)
            counts = [count_chars(x.body, True) for x in secs]  # 합계는 항상 공백 포함
            actual = sum(counts)
            used = min(actual, lore_cap) if key == normalize("로어북") else actual
            if key == normalize("로어북") and lore_cap:
                note = f"최대 {lore_cap:,}자 반영"
        total += used
        parts.append(SizePart(field, label, used, actual, counts, found, note))
    return parts, total


def _check_total_length(sections: list[Section], config: dict) -> list[CheckItem]:
    rule = config["합계_제한"]
    recommended = int(rule.get("권장", 20000))
    maximum = int(rule.get("최대", 30000))
    parts, total = setting_size(sections, config)
    is_card = lambda p: normalize(p.field) in (normalize(config.get("카드_항목") or "\0"), normalize(config.get("조연_항목") or "\0"))
    texts = []
    for p in parts:
        text = f"{p.label} {p.used:,}"
        extra = [p.note] if p.note else []
        if p.actual > p.used:
            extra.append(f"실제 {p.actual:,}")
        if extra:
            text += "(" + ", ".join(extra) + ")"
        if len(p.counts) > 1:
            text += (" [" if is_card(p) else f" [{len(p.counts)}개 섹션: ") + " + ".join(f"{c:,}" for c in p.counts) + "]"
        texts.append(text)
    missing = [p.field for p in parts if not p.found and not (normalize(p.field) == normalize(config.get("조연_항목") or "\0"))]

    detail = " / ".join(texts) + f" = {total:,}자 (공백 포함)"
    tokens = token_text(total, config)
    if tokens:
        detail += f"\n예상 토큰: {tokens} (MELLOY 표시와 다를 수 있는 추정치)"
    detail += f"\n권장 {recommended:,}자 / 최대 {maximum:,}자"
    if missing:
        detail += "\n찾지 못한 섹션(0자로 계산): " + ", ".join(f"'{m}'" for m in missing)

    if total > maximum:
        return [CheckItem(FAIL, f"설정 크기가 최대를 넘었습니다 (+{total - maximum:,}자)", detail)]
    if total > recommended:
        return [CheckItem(WARN, f"설정 크기가 권장을 넘었습니다 (+{total - recommended:,}자)", detail)]
    return [CheckItem(PASS, "설정 크기 정상", detail)]


VAR_TAG = re.compile(r"^\s*\[([^\]]+)\]\s*(.*)$")


def _var_field(lines: list[tuple[int, str]], tag: str) -> tuple[str, int] | None:
    """블록 안에서 [tag] 뒤의 텍스트를 다음 '[' 로 시작하는 줄 전까지 모읍니다. (텍스트, 줄 번호)"""
    for k, (no, line) in enumerate(lines):
        m = VAR_TAG.match(line)
        if not m or m.group(1).strip() != tag:
            continue
        body = [m.group(2)]
        for _, nxt in lines[k + 1:]:
            if nxt.lstrip().startswith(("[", "#")):
                break
            body.append(nxt)
        return "\n".join(body).strip(), no
    return None


def _var_lines(lines: list[tuple[int, str]], tag: str) -> list[tuple[int, str]]:
    """[tag] 아래의 줄들을 (줄 번호, 내용)으로 돌려줍니다. 빈 줄은 뺍니다."""
    for k, (no, line) in enumerate(lines):
        m = VAR_TAG.match(line)
        if not m or m.group(1).strip() != tag:
            continue
        result = [(no, m.group(2).strip())] if m.group(2).strip() else []
        for nno, nxt in lines[k + 1:]:
            if nxt.lstrip().startswith(("[", "#")):
                break
            if nxt.strip():
                result.append((nno, nxt.strip()))
        return result
    return []


def _check_variables(sections: list[Section], config: dict) -> list[CheckItem]:
    rule = config["변수_검사"]
    limit = int(rule.get("변수_내용_최대", 200))
    no_space = bool(rule.get("변수_이름_공백_금지", True))

    found = _matching_sections(sections, ["변수"])
    found = [s for s in found if normalize(s.name) == normalize("변수")] or found
    if not found:
        return [CheckItem(WARN, "'변수' 섹션을 찾지 못해 변수 검사를 건너뜁니다", "헤더를 '# 변수' 로 적어 주세요.")]

    # '[변수 이름]' 줄마다 블록을 나눕니다.
    blocks: list[list[tuple[int, str]]] = []
    for sec in found:
        for offset, line in enumerate(sec.body.splitlines()):
            no = sec.start_line + offset
            m = VAR_TAG.match(line)
            if m and m.group(1).strip() == "변수 이름":
                blocks.append([])
            if blocks:
                blocks[-1].append((no, line))
    if not blocks:
        return [CheckItem(WARN, "'변수' 섹션에 [변수 이름] 블록이 없습니다", "각 변수는 '[변수 이름] 이름 / 텍스트' 줄로 시작해야 합니다.")]

    items: list[CheckItem] = []
    name_fail = len_fail = row_warn = 0
    longest = ("", 0)
    kinds = {"텍스트": 0, "게이지": 0}
    for block in blocks:
        head_no, head = block[0]
        raw = VAR_TAG.match(head).group(2).strip()
        name, sep, kind = raw.rpartition("/")
        name, kind = (name.strip(), kind.strip()) if sep else (raw, "")
        label = name or "(이름 없음)"

        if kind not in kinds:
            items.append(CheckItem(WARN, f"변수 '{label}'의 종류를 알 수 없습니다",
                                   f"{head_no}번째 줄 — '이름 / 텍스트' 또는 '이름 / 게이지' 형식으로 적어 주세요."))
        else:
            kinds[kind] += 1

        if no_space and re.search(r"\s", name):
            name_fail += 1
            items.append(CheckItem(FAIL, f"변수 이름 '{name}'에 공백이 있습니다",
                                   f"{head_no}번째 줄 — 공백 대신 밑줄(_)을 쓰거나 붙여 써 주세요."))

        tag = "값이 변하는 조건" if kind == "게이지" else "변수 내용"
        field = _var_field(block, tag)
        if field is None:
            items.append(CheckItem(WARN, f"변수 '{label}'에 [{tag}] 칸이 없습니다", f"{head_no}번째 줄의 변수"))
        else:
            text, no = field
            n = ulen(text)  # 공백 포함
            if n > longest[1]:
                longest = (label, n)
            if n > limit:
                len_fail += 1
                items.append(CheckItem(FAIL, f"변수 '{label}'의 [{tag}]이 {n:,}자입니다 (최대 {limit:,}자, +{n - limit:,}자)",
                                       f"{no}번째 줄"))

        for no, row in _var_lines(block, "구간별 행동"):
            if ulen(row) > limit:
                row_warn += 1
                items.append(CheckItem(WARN, f"변수 '{label}'의 [구간별 행동] 한 줄이 {ulen(row):,}자입니다 (기준 {limit:,}자)",
                                       f"{no}번째 줄 — {row[:20]}..."))

    summary = f"텍스트 {kinds['텍스트']}개 / 게이지 {kinds['게이지']}개"
    ok = []
    count_rules = [(k, rule.get(f"{k}_최대")) for k in ("텍스트", "게이지") if rule.get(f"{k}_최대") is not None]
    over = [(k, kinds[k], int(m)) for k, m in count_rules if kinds[k] > int(m)]
    for k, n, m in over:
        items.insert(0, CheckItem(FAIL, f"{k} 변수가 {n}개입니다 (최대 {m}개)", "플랫폼에 등록할 수 있는 개수를 넘었습니다."))
    if count_rules and not over:
        ok.append(CheckItem(PASS, "변수 개수가 한도 안입니다",
                            " / ".join(f"{k} {kinds[k]}/{int(m)}개" for k, m in count_rules)))
    if no_space and name_fail == 0:
        ok.append(CheckItem(PASS, f"변수 이름에 공백이 없습니다 ({len(blocks)}개)", summary))
    if len_fail == 0:
        detail = f"가장 긴 변수: '{longest[0]}' {longest[1]:,}자" if longest[0] else ""
        ok.append(CheckItem(PASS, f"변수 내용이 모두 {limit:,}자 이내입니다", detail))
    if kinds["게이지"] and row_warn == 0:
        ok.append(CheckItem(PASS, f"[구간별 행동]의 모든 줄이 {limit:,}자 이내입니다"))
    return ok + items


# ── 다인챗 검사 (검수설정.json 에 항목이 있을 때만) ──
def _card_sections(sections: list[Section], config: dict, item: str | None = None) -> tuple[Section | None, list[Section]]:
    """결과물에서 카드 항목(예: # 등장인물, # 조연)과 그 아래 카드들(## 이름)을 찾습니다."""
    name = item or config.get("카드_항목")
    found = _matching_sections(sections, _field_aliases(name, config)) if name else []
    if not found:
        return None, []
    cards = []
    for part in found:
        inner = [s for s in sections if s.level > part.level and part.start_line <= s.start_line - 1 <= part.end_line]
        level = min((s.level for s in inner), default=0)
        cards += [s for s in inner if s.level == level]
    return found[0], cards


def _card_name(heading: str) -> str:
    return re.sub(r"\s*[(（].*$", "", heading).strip()


def _card_template_subs(config: dict) -> list[str]:
    path = rules_dir() / (config.get("템플릿_파일") or TEMPLATE_FILE_DEFAULT)
    if not path.exists():
        return []
    try:
        schema = build_schema(read_text_file(path), config)
    except Exception:
        return []
    return [f.title for p in schema.parts if p.kind == "cards" and not p.optional for f in p.card_fields if f.title]


def _check_cards(sections: list[Section], config: dict) -> list[CheckItem]:
    rule = dict(config.get("카드_글자수") or {})
    rule.setdefault("최대", 5000)
    limit = int(rule["최대"])
    goal, goal_name = recommended_line(rule, config)
    part, cards = _card_sections(sections, config)
    title = config.get("카드_항목")
    if part is None:
        return [CheckItem(WARN, f"'{title}' 항목을 찾지 못해 카드 검사를 건너뜁니다", f"헤더를 '# {title}' 로 적어 주세요.")]
    if not cards:
        return [CheckItem(FAIL, "등장인물 카드가 한 장도 없습니다", "카드마다 '## 이름' 제목으로 시작해 주세요.")]
    sub_title = config.get("조연_항목")
    sub_cards = _card_sections(sections, config, sub_title)[1] if sub_title else []
    items, sizes, size_problem = [], [], False
    required = _card_template_subs(config)
    missing_any = False
    for c in cards + sub_cards:
        label = _card_name(c.name) or c.name
        if c in sub_cards:
            label += f"({sub_title})"
        n = count_chars(c.body, True)
        sizes.append(f"{label} {n:,}")
        where = f"{c.start_line - 1}번째 줄"
        if n > limit:
            size_problem = True
            items.append(CheckItem(FAIL, f"'{label}' 카드가 {n:,}자입니다 (최대 {limit:,}자, +{n - limit:,}자)", where))
        elif n > goal:
            size_problem = True
            items.append(CheckItem(WARN, f"'{label}' 카드가 {goal_name}선을 넘었습니다 ({n:,}자 / {goal_name} {goal:,}자)", where))
        subs = [s for s in sections if s.level > c.level and c.start_line <= s.start_line - 1 <= c.end_line]
        missing = [r for r in required if not any(normalize(s.name) == normalize(r) for s in subs)]
        if missing:
            missing_any = True
            items.append(CheckItem(FAIL, f"'{label}' 카드에 없는 칸: " + ", ".join(missing), where))
    head = []
    count = f"카드 {len(cards)}장" + (f"(조연 {len(sub_cards)}장 별도)" if sub_cards else "")
    if not size_problem:
        head.append(CheckItem(PASS, f"{count} 모두 {goal_name} {goal:,}자 이내입니다 (최대 {limit:,}자)", " / ".join(sizes)))
    if required and not missing_any:
        head.append(CheckItem(PASS, f"모든 카드에 칸 {len(required)}개가 있습니다", ", ".join(required)))
    items = head + items
    if config.get("캐스트_명단_대조"):
        items += _check_cast_list(sections, cards, sub_cards)
    return items


def _cast_names(text: str) -> list[str]:
    names = []
    for item in re.split(r"[／;；]", text):
        item = item.strip().lstrip("-·*•").strip()
        name = re.split(r"[(（—–,/|]| - ", item)[0].strip().strip("\"'“”「」")
        if name and not name.startswith("(") and len(name) <= 12 and name not in ("이름",):
            names.append(name)
    return names


def _check_cast_list(sections: list[Section], cards: list[Section], sub_cards: list[Section] = ()) -> list[CheckItem]:
    """설명 필드 '캐스트 구성'의 명단과 카드 이름이 서로 맞는지 봅니다. (조연 카드는 명단에 없어도 됨)"""
    cast = next((s for s in sections if "캐스트" in normalize(s.name)), None)
    if cast is None:
        return []
    listed, on = [], False
    for line in cast.body.splitlines():
        st = line.strip()
        if re.match(r"^[-*•]?\s*명단\s*[:：]", st):
            on = True
            listed += _cast_names(re.split(r"[:：]", st, 1)[1])
            continue
        if on:
            if not st:
                continue
            if st.startswith("#") or (line[:1] not in (" ", "\t") and st.startswith(("-", "*", "•"))):
                break
            listed += _cast_names(st)
    card_names = [n for n in (_card_name(c.name) for c in cards) if n and not n.startswith("(")]
    if not listed or not card_names:
        return []
    key = lambda n: _compact(n).lower()
    all_names = card_names + [_card_name(c.name) for c in sub_cards]
    no_card = [n for n in listed if key(n) not in {key(c) for c in all_names}]
    no_list = [c for c in card_names if key(c) not in {key(n) for n in listed}]
    items = []
    if no_card:
        items.append(CheckItem(WARN, "명단에는 있지만 카드가 없는 인물이 있습니다", ", ".join(no_card)))
    if no_list:
        items.append(CheckItem(WARN, "카드는 있지만 '캐스트 구성' 명단에 없는 인물이 있습니다", ", ".join(no_list)))
    if not items:
        items.append(CheckItem(PASS, f"캐스트 명단과 카드 이름이 일치합니다 ({len(card_names)}명)"))
    return items


DIVIDER = re.compile(r"^\s*([-─━=═~*_·•+])\1{3,}\s*$")


def _check_lorebook(sections: list[Section], config: dict) -> list[CheckItem]:
    rule = config["로어북_검사"]
    found = _matching_sections(sections, ["로어북"])
    if not found:
        return [CheckItem(WARN, "'로어북' 섹션을 찾지 못해 로어북 검사를 건너뜁니다", "헤더를 '# 로어북' 으로 적어 주세요.")]
    lines = [(sec.start_line + i, l) for sec in found for i, l in enumerate(sec.body.splitlines())]
    items = []
    max_div = int(rule.get("구분선_최대", 12))
    long = [no for no, l in lines if DIVIDER.match(l) and len(l.strip()) > max_div]
    if long:
        items.append(CheckItem(WARN, f"{max_div}자를 넘는 구분선이 있습니다",
                               f"{fmt_lines(long)} — 코드블록은 자동 줄바꿈이 안 되므로 구분선을 {max_div}자 이내로 줄여 주세요."))
    else:
        items.append(CheckItem(PASS, f"구분선이 모두 {max_div}자 이내입니다"))
    if rule.get("명령어_형식_필수", True):
        entries: list[tuple[int, str, list[str]]] = []
        for no, l in lines:
            m = LORE_HEAD.match(l)
            if m:
                entries.append((no, m.group(2).strip(), []))
            elif entries and not l.lstrip().startswith("#"):
                entries[-1][2].append(l)
        missing = []
        for no, trigger, body in entries:
            if not trigger.startswith("!"):
                continue
            fmt = next((m.group(2) for m in (VAR_TAG.match(b) for b in body) if m and m.group(1).strip() == "출력 형식"), "")
            if ("코드블록" in fmt) == ("마크다운" in fmt):  # 둘 다 없거나, '(코드블록 / 마크다운)' 처럼 안 고른 경우
                missing.append((no, trigger))
        if missing:
            items.append(CheckItem(WARN, "출력 형식(코드블록 / 마크다운)을 정하지 않은 명령어가 있습니다",
                                   ", ".join(t for _, t in missing) + f" — {fmt_lines([n for n, _ in missing])}"))
        elif any(t.startswith("!") for _, t, _ in entries):
            items.append(CheckItem(PASS, "명령어마다 출력 형식이 정해져 있습니다"))
    return items


TURN_TIME = re.compile(r"턴\s*(?:에|당|마다)\s*[^\n.]{0,12}?\d+\s*(?:~\s*\d+\s*)?(?:분|시간)")


def _check_turn_time(text: str) -> list[CheckItem]:
    hits = [line_of(text, m.start()) for m in TURN_TIME.finditer(text)]
    if hits:
        return [CheckItem(WARN, "턴 단위 시간 표현이 있습니다",
                          f"{fmt_lines(hits)} — '한 턴에 ○분' 대신 서술된 행동의 소요 시간대로 흐르게 써 주세요.")]
    return [CheckItem(PASS, "턴 단위 시간 표현이 없습니다")]


def _template_headers(config: dict) -> list[str]:
    name = config.get("템플릿_파일")
    if not name or not config.get("템플릿_헤더_필수", True):
        return []
    path = rules_dir() / name
    if not path.exists():
        return []
    names = []
    for s in parse_sections(read_text_file(path)):
        # '(조직/가문)', '3-1. (본편 루트)' 처럼 괄호 안은 채워 넣을 자리이므로 빼고 비교합니다.
        # 괄호를 빼면 남는 게 없는 제목은 결과물마다 이름이 달라지므로 필수 헤더에서 제외합니다.
        name = re.sub(r"\([^)]*\)", "", s.name).strip()
        if normalize(name) and normalize(name) != normalize(config.get("조연_항목") or "\0"):  # 조연은 없어도 됨
            names.append(name)
    return names


def _check_sections(res: list[Section], orig: list[Section], config: dict) -> list[CheckItem]:
    items = []
    res_names = {normalize(s.name) for s in res}

    if not res:
        items.append(CheckItem(FAIL, "생성 결과에서 섹션 헤더를 하나도 찾지 못했습니다",
                               "헤더는 '# 제목' 형태로 한 줄에 단독으로 적어 주세요."))
        return items

    required: list[tuple[str, str]] = [(n, "필수 섹션") for n in config.get("필수_섹션", [])]
    required += [(n, f"템플릿({config.get('템플릿_파일')})") for n in _template_headers(config)]
    seen = set()
    missing = 0
    for name, source in required:
        if normalize(name) in seen:
            continue
        seen.add(normalize(name))
        if find_section(res, [name]) is None:
            missing += 1
            items.append(CheckItem(FAIL, f"'{name}' 섹션이 없습니다", f"기준: {source}"))
    if required and missing == 0:
        items.append(CheckItem(PASS, f"필수 섹션 {len(seen)}개가 모두 있습니다", ", ".join(n for n, _ in required[:20])))

    if config.get("원본_헤더_비교", True):
        if not orig:
            items.append(CheckItem(INFO, "원본 설정에서 헤더를 찾지 못해 헤더 비교를 건너뜁니다"))
        else:
            lost = [s.name for s in orig if normalize(s.name) not in res_names]
            if lost:
                items.append(CheckItem(WARN, f"원본에 있던 헤더 {len(lost)}개가 결과에 없습니다",
                                       ", ".join(f"'{n}'" for n in lost[:20])))
            else:
                items.append(CheckItem(PASS, f"원본의 헤더 {len(orig)}개가 결과에 모두 있습니다"))
    return items


# ════════════════════════════════════════════════════════════
#  2단계: AI 의미 검수 (Claude Code CLI)
# ════════════════════════════════════════════════════════════
# 명령줄 인자로는 짧은 지시문만 넘기고, 긴 본문은 표준입력으로 전달합니다.
# (Windows 명령줄 길이 제한과 특수문자 문제를 피하기 위해서입니다.)
CLI_INSTRUCTION = "표준입력으로 전달된 검수 요청서를 읽고 그 지시에 따라 JSON 배열만 출력하세요"


def load_rule_texts() -> list[tuple[str, str]]:
    """지금 모드의 규칙 폴더와 공통 폴더의 .md / .txt 를 모두 읽습니다. (하위 폴더는 제외)"""
    texts = []
    for folder in (rules_dir(), COMMON_DIR):
        if folder.is_dir():
            for p in sorted(folder.iterdir()):
                if p.is_file() and p.suffix.lower() in (".md", ".txt"):
                    texts.append((p.name, read_text_file(p)))
    return texts


def build_ai_prompt(original: str, result: str) -> str:
    rules = load_rule_texts()
    rule_block = "\n\n".join(f"### 규칙 파일: {name}\n{body.strip()}" for name, body in rules) or "(규칙 파일 없음)"
    if rule_mode() == "다인":
        extra_item = ("\n5. 규칙 위반(다인챗): 규칙 파일의 원칙과 '최종 점검' 항목을 결과가 어긴 곳. "
                      "예) 같은 정보가 설명 필드·카드·로어북 중 두 곳 이상에 있음, 기획서 잔재, 카드 [평소]에 {{user}}에게 먼저 하는 행동이 없음, "
                      "정보 경계의 비밀 분포와 카드 내용이 어긋남, 세이프티 락·상태창 출력 지시가 들어감, 다른 카드·필드를 번호로 가리킴.")
    else:
        extra_item = ("\n5. 규칙 위반: 규칙 파일(작업지침의 판정 기준, 플랫폼 문체지침 등)을 결과가 어긴 곳. "
                      "예) 세이프티 락·상태창 출력 지시가 들어감, 변수·로어북에 {{user}} 표기, 게이지 구간 누락, "
                      "플랫폼 [나레이션]과 충돌하는 문체(현재형 지문, 감정에 이름 붙이기, 서술자의 심리 설명, 대사를 별표·괄호로 감싸기).")
    kinds = "누락|약화|환각|왜곡|규칙|정상"
    return f"""너는 캐릭터 설정 검수관이다. 아래 [원본 설정]을 기준으로 [생성 결과]를 의미 단위로 대조해서 문제를 찾아라.
파일을 읽거나 도구를 쓸 필요는 없다. 아래에 주어진 내용만으로 판단하라.

## 규칙 파일 (rules 폴더)
{rule_block}

## 검사 항목
1. 누락: 원본에 있는 설정(성격, 관계, 말투, 금지 규칙, 수치, 고유명사 등)이 결과에서 빠졌는가.
2. 약화(기계어치환 원칙 위반): 원본의 금지/필수 규칙이 결과에서 더 약한 강도로 바뀌었는가.
   예) "절대 X하지 않는다" → "X를 선호하지 않는다", "가급적 X하지 않는다", "X를 피하는 편이다"
   예) "반드시 Y한다" → "Y하는 경우가 많다"
3. 환각: 원본에 없는 설정(새 인물, 과거사, 능력, 관계, 수치 등)이 결과에 임의로 추가되었는가.
4. 왜곡: 원본과 의미가 달라지거나 반대로 바뀐 설정이 있는가.{extra_item}

## 출력 형식 (반드시 지킬 것)
- 설명 문장이나 코드블록 없이 JSON 배열 하나만 출력한다.
- 각 원소: {{"구분": "{kinds}", "심각도": "실패|경고|정상", "항목": "짧은 제목", "원본": "관련 원본 문구(없으면 빈 문자열)", "결과": "관련 결과 문구(없으면 빈 문자열)", "설명": "왜 문제인지 한두 문장"}}
- 금지 규칙 약화와 명백한 누락·환각은 "실패", 애매하거나 사소한 것은 "경고"로 한다.
- 문제가 하나도 없으면 [{{"구분": "정상", "심각도": "정상", "항목": "의미 검수 통과", "원본": "", "결과": "", "설명": "누락·약화·환각이 발견되지 않았습니다."}}] 를 출력한다.
- 모든 문장은 한국어로 쓴다.

## 원본 설정
<<<원본 시작>>>
{original.strip()}
<<<원본 끝>>>

## 생성 결과
<<<결과 시작>>>
{result.strip()}
<<<결과 끝>>>
"""


def _claude_install_candidates() -> list[Path]:
    """PATH 에 아직 등록되지 않았을 때를 대비한 공식 설치 위치 목록입니다. (화면에는 표시하지 않음)"""
    if sys.platform != "win32":
        return [Path.home() / ".local" / "bin" / "claude"]
    places = [
        ("USERPROFILE", (".local", "bin", "claude.exe")),  # 공식 설치 프로그램(install.ps1 / install.cmd)
        ("LOCALAPPDATA", ("Microsoft", "WinGet", "Links", "claude.exe")),  # winget
        ("APPDATA", ("npm", "claude.cmd")),  # npm
    ]
    return [Path(os.environ[var], *parts) for var, parts in places if os.environ.get(var)]


def find_claude() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    for path in _claude_install_candidates():
        if path.is_file():
            return str(path)
    return None


def parse_ai_output(text: str) -> list[CheckItem] | None:
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    start, end = cleaned.find("["), cleaned.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list):
        return None
    status_map = {"실패": FAIL, "경고": WARN, "정상": PASS}
    items = []
    for d in data:
        if not isinstance(d, dict):
            continue
        status = status_map.get(str(d.get("심각도", "")).strip(), WARN)
        kind = str(d.get("구분", "")).strip()
        title = str(d.get("항목", "")).strip() or "(제목 없음)"
        if kind and kind != "정상":
            title = f"[{kind}] {title}"
        lines = []
        if d.get("원본"):
            lines.append(f"원본: {d['원본']}")
        if d.get("결과"):
            lines.append(f"결과: {d['결과']}")
        if d.get("설명"):
            lines.append(str(d["설명"]))
        items.append(CheckItem(status, title, "\n".join(lines)))
    return items or None


class AiWorker(QThread):
    """claude -p 를 백그라운드로 실행합니다. (화면이 멈추지 않도록 별도 스레드)"""

    done = Signal(bool, str)  # (성공 여부, 출력 또는 오류 메시지)

    def __init__(self, prompt: str, timeout: int, instruction: str = CLI_INSTRUCTION):
        super().__init__()
        self.prompt = prompt
        self.instruction = instruction
        self.timeout = timeout
        self.proc: subprocess.Popen | None = None
        self.cancelled = False

    def run(self):
        exe = find_claude()
        if not exe:
            self.done.emit(False, CLAUDE_MISSING_MSG)
            return
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            self.proc = subprocess.Popen(
                [exe, "-p", self.instruction],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
                creationflags=flags,
            )
            out, err = self.proc.communicate(self.prompt, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.communicate()
            self.done.emit(False, f"AI 응답이 {self.timeout}초 안에 오지 않아 중단했습니다.\n"
                                  f"rules/검수설정.json 의 'AI_제한시간_초' 값을 늘려 보세요.")
            return
        except Exception as e:
            self.done.emit(False, f"Claude Code 를 실행하지 못했습니다.\n{e}")
            return

        if self.cancelled:
            self.done.emit(False, "사용자가 AI 작업을 중지했습니다.")
        elif self.proc.returncode != 0:
            tail = (err or out or "").strip()[-1500:]
            hint = ""
            if re.search(r"log ?in|auth|api key|credential", tail, re.IGNORECASE):
                hint = "\n\n→ 로그인이 필요해 보입니다. 명령 프롬프트에서 claude 를 한 번 실행해 로그인해 주세요."
            self.done.emit(False, f"Claude Code 가 오류로 종료되었습니다. (종료 코드 {self.proc.returncode})\n{tail}{hint}")
        else:
            self.done.emit(True, out)

    def cancel(self):
        self.cancelled = True
        if self.proc and self.proc.poll() is None:
            self.proc.kill()


CLAUDE_MISSING_MSG = (
    "Claude Code(claude 명령)를 찾지 못했습니다.\n\n"
    "AI 의미 검수를 쓰려면 Claude Code 를 설치하고 로그인해야 합니다.\n"
    "1) Windows PowerShell 을 열고 아래 한 줄을 붙여넣어 설치합니다.\n"
    "   irm https://claude.ai/install.ps1 | iex\n"
    "2) 설치가 끝나면 명령 프롬프트에서 claude 를 입력해 로그인합니다.\n"
    "3) 이 프로그램을 껐다가 다시 실행합니다.\n\n"
    "※ Claude 데스크톱 앱만 설치한 경우에는 claude 명령이 생기지 않습니다. 위 1)을 따로 해 주세요.\n"
    "※ Claude Code 는 Pro·Max·Team·Enterprise 요금제 또는 Console 계정이 필요합니다. (무료 요금제 불가)\n"
    "※ 기계 검사는 Claude Code 없이도 사용할 수 있습니다."
)


# ════════════════════════════════════════════════════════════
#  작성: 템플릿 양식 분석 / 원자료 채우기 / 변환 파일 조립 (AI 없음)
# ════════════════════════════════════════════════════════════
TEMPLATE_FILE_DEFAULT = "템플릿.md"
SOURCE_FORM_FILE = Path("원자료양식.txt")
OUTPUT_DIR = Path("생성결과")
VAR_HEAD = re.compile(r"^\s*\[\s*변수 이름\s*\]\s*(.*)$")
LORE_HEAD = re.compile(r"^\s*(\d+)\s*[.)]\s*트리거\s*[:：]\s*(.*)$")
BULLETS = ("-", "·", "*", "•", "※", "+")
SOURCE_FORM_NOTE = [
    "※ MELLOY 원자료 양식입니다. '등장인물', '0. Core' 같은 제목 줄은 지우거나 고치지 말고, 각 제목 아래에 내용을 채워 주세요.",
    "※ 괄호 ( ) 안 설명이나 'Age:' 처럼 비어 있는 칸은 실제 내용으로 바꿔 주세요.",
    "※ 변수와 로어북은 [변수 이름], [변수 내용], '1. 트리거:' 같은 표시를 그대로 두고 내용만 채워 주세요.",
]


CARD_HEAD = re.compile(r"^\s*■\s*(캐릭터|주연|조연)\s*[:：]\s*(.*)$")  # 원자료(자연어)에서 카드 시작 줄 (조연은 조연 카드)


@dataclass
class ProseField:
    part: str  # 큰 항목 (# 제목, 예: 등장인물)
    title: str  # ## 제목 (예: 0. Core). 큰 항목 바로 아래 머리말이면 ''
    guide: str  # 템플릿에 적힌 안내 내용 (※ 메모 줄은 뺀 것)
    sub_heads: list[str]  # 칸 안의 ### 제목들
    notes: list[str] = dc_field(default_factory=list)  # 템플릿의 ※ 메모 줄 (결과물에는 넣지 않음)

    @property
    def key(self) -> tuple[str, str]:
        return (self.part, self.title)

    @property
    def label(self) -> str:
        return self.title or f"{self.part} 머리말"


@dataclass
class TagBlock:
    """변수 블록 하나([변수 이름] ...) 또는 로어북 항목 하나(N. 트리거: ...)"""
    group: str  # 변수: 종류(텍스트/게이지), 로어북: 분류(## 제목)
    name: str  # 변수 이름 / 로어북 트리거
    values: dict[str, str]  # {태그: 내용}


@dataclass
class BlockGroup:
    title: str  # 출력할 ## 제목 (예: 텍스트 변수, 필수 명령어)
    kind: str  # 블록의 group 값 (변수: 텍스트/게이지, 로어북: 제목과 같음)
    tags: list[str]  # 태그 순서
    multiline: set[str]  # 태그 다음 줄부터 내용을 쓰는 태그 (예: 구간별 행동)
    short: set[str]  # 한 줄짜리 짧은 태그 (예: 글자수 상한)
    sample: str  # 템플릿 속 첫 블록 원문 (AI 안내용)


@dataclass
class TemplatePart:
    title: str
    kind: str  # 'prose' / 'var' / 'lore' / 'cards'
    fields: list[ProseField]
    groups: list[BlockGroup]
    defaults: list[TagBlock]
    card_fields: list[ProseField] = dc_field(default_factory=list)  # 카드 한 장의 칸 구성 (### 제목)
    card_title: str = ""  # 템플릿 속 카드 이름 자리 (예: (이름) (한자 / 로마자))
    notes: list[str] = dc_field(default_factory=list)  # 큰 항목 바로 아래 ※ 메모
    optional: bool = False  # 조연 카드처럼 한 장도 없어도 되는 카드 항목


@dataclass
class TemplateSchema:
    parts: list[TemplatePart]
    lines: set[str]  # 템플릿의 모든 줄 (안내 문구가 남았는지 확인용)
    note_lines: set[str] = dc_field(default_factory=set)  # ※ 메모 줄 (불러올 때 걸러 냄)

    def part(self, title: str) -> TemplatePart | None:
        return next((p for p in self.parts if p.title == title), None)


_card_uid = 0


@dataclass
class CardData:
    """등장인물 카드 한 장 (다인챗)"""
    name: str
    sections: dict[str, str]  # {### 제목: 내용}
    uid: int = 0

    def __post_init__(self):
        global _card_uid
        if not self.uid:
            _card_uid += 1
            self.uid = _card_uid

    @property
    def label(self) -> str:
        return re.sub(r"\s*\(.*$", "", self.name).strip() or self.name.strip() or "(이름 없음)"


@dataclass
class FormData:
    prose: dict[tuple[str, str], str]
    blocks: dict[str, list[TagBlock]]  # 큰 항목 제목 → 블록들
    cards: dict[str, list[CardData]] = dc_field(default_factory=dict)  # 큰 항목 제목 → 카드들


def _heading_key(line: str) -> str:
    """제목 줄로 볼 수 있으면 비교용 키를, 아니면 '' 를 돌려줍니다. ('#' 은 있어도 없어도 됨)"""
    s = line.strip()
    if not s or (s.startswith(BULLETS) and not s.startswith("#")):
        return ""
    s = s.lstrip("#").strip()
    return normalize(s) if 0 < len(s) <= 60 else ""


def _compact(s: str) -> str:
    return re.sub(r"\s", "", s)


def _split_notes(lines: list[str]) -> tuple[list[str], list[str]]:
    """※ 로 시작하는 메모 줄과 나머지 줄을 나눕니다. (내용 줄, 메모 줄)"""
    body, notes = [], []
    for line in lines:
        (notes if line.strip().startswith("※") else body).append(line)
    return body, notes


def _parse_tag_body(lines: list[str]) -> tuple[dict[str, str], list[str], set[str]]:
    """[태그] 줄들을 {태그: 내용} 으로 읽습니다. (내용, 태그 순서, 다음 줄부터 쓰는 태그)"""
    values: dict[str, str] = {}
    order: list[str] = []
    multi: set[str] = set()
    cur, buf, inline = None, [], ""

    def flush():
        if cur is None:
            return
        rest = list(buf)
        if not inline.strip() and any(b.strip() for b in rest):
            multi.add(cur)
        values[cur] = "\n".join(([inline.strip()] if inline.strip() else []) + rest).strip()

    for line in lines:
        m = VAR_TAG.match(line)
        if m:
            flush()
            cur, inline, buf = m.group(1).strip(), m.group(2), []
            order.append(cur)
        elif cur is not None:
            buf.append(line.rstrip())
    flush()
    return values, order, multi


def _split_blocks(lines: list[str], kind: str, group_titles: list[str]) -> list[tuple[str, TagBlock, list[str], set[str]]]:
    """변수/로어북 본문을 블록으로 나눕니다. [(소속 ## 제목, 블록, 태그 순서, 여러 줄 태그), ...]"""
    group_keys = {normalize(g): g for g in group_titles}
    heading = group_titles[0] if group_titles else ""
    result = []
    head: tuple[str, str] | None = None
    body: list[str] = []

    def close():
        if head is None:
            return
        values, order, multi = _parse_tag_body(body)
        group, name = head
        if kind == "lore":
            group = heading
        result.append((heading, TagBlock(group, name, values), order, multi))

    for line in lines:
        hk = _heading_key(line)
        if hk and hk in group_keys and not VAR_HEAD.match(line) and not LORE_HEAD.match(line):
            close()
            head, body = None, []
            heading = group_keys[hk]
            continue
        m = VAR_HEAD.match(line) if kind == "var" else LORE_HEAD.match(line)
        if m:
            close()
            body = []
            if kind == "var":
                name, sep, typ = m.group(1).strip().rpartition("/")
                head = (typ.strip(), name.strip()) if sep else ("", m.group(1).strip())
            else:
                head = ("", m.group(2).strip())
            continue
        if head is not None:
            body.append(line)
    close()
    return result


def _block_text(block: TagBlock, group: BlockGroup | None, kind: str, number: int = 0) -> list[str]:
    if kind == "var":
        lines = [f"[변수 이름] {block.name} / {block.group}" if block.group else f"[변수 이름] {block.name}"]
    else:
        lines = [f"{number}. 트리거: {block.name}".rstrip()]
    tags = list(group.tags) if group else []
    tags += [t for t in block.values if t not in tags]
    for tag in tags:
        value = block.values.get(tag, "")
        if (group and tag in group.multiline) or "\n" in value:
            lines.append(f"[{tag}]")
            if value:
                lines.append(value)
        else:
            lines.append(f"[{tag}] {value}".rstrip())
    return lines


def load_template_schema(config: dict) -> tuple[TemplateSchema | None, str]:
    """rules 폴더의 템플릿을 읽어 작성 양식을 만듭니다. (양식, 오류 메시지)"""
    path = rules_dir() / (config.get("템플릿_파일") or TEMPLATE_FILE_DEFAULT)
    if not path.exists():
        return None, f"템플릿 파일({path})이 없습니다. rules 폴더에 템플릿을 넣어 주세요."
    try:
        return build_schema(read_text_file(path), config), ""
    except Exception as e:
        return None, f"템플릿 파일({path})을 읽지 못했습니다: {e}"


def _prose_fields(part_title: str, sec: Section, children: list[Section]) -> list[ProseField]:
    """sec 본문을 머리말 + 하위 제목(children) 칸으로 나눕니다."""
    body_lines = sec.body.splitlines()
    fields = []
    first = (children[0].start_line - 1 - sec.start_line) if children else len(body_lines)
    intro_lines, intro_notes = _split_notes(body_lines[:first])
    intro = "\n".join(intro_lines).strip("\n")
    if intro.strip() or not children:
        fields.append(ProseField(part_title, "", intro, [], intro_notes))
    for c in children:
        lines, notes = _split_notes(c.body.splitlines())
        subs = [m.group(2).strip() for m in (MD_HEADER.match(l) for l in lines) if m]
        fields.append(ProseField(part_title, c.name, "\n".join(lines).strip("\n").rstrip(), subs, notes))
    return fields


def build_schema(text: str, config: dict | None = None) -> TemplateSchema:
    secs = parse_md_sections(text)
    if not secs:
        raise ValueError("'# 제목' 으로 된 큰 항목이 없습니다.")
    card_part = normalize((config or {}).get("카드_항목") or "")
    sub_part = normalize((config or {}).get("조연_항목") or "")
    top_level = min(s.level for s in secs)
    parts = []
    all_notes: list[str] = []
    for top in (s for s in secs if s.level == top_level):
        inner = [s for s in secs if s.level > top_level and top.start_line <= s.start_line - 1 <= top.end_line]
        child_level = min((s.level for s in inner), default=0)
        children = [s for s in inner if s.level == child_level]
        body_lines, part_notes = _split_notes(top.body.splitlines())
        all_notes += part_notes
        if normalize(top.name) in (normalize("변수"), normalize("로어북")):
            kind = "var" if normalize(top.name) == normalize("변수") else "lore"
            titles = [c.name for c in children]
            split = _split_blocks(body_lines, kind, titles)
            groups: dict[str, BlockGroup] = {}
            for heading, block, order, multi in split:
                gkey = block.group if kind == "var" else heading
                g = groups.get(gkey)
                if g is None:
                    g = groups[gkey] = BlockGroup(heading if kind == "var" else gkey, gkey, list(order), set(multi), set(),
                                                  "\n".join(_block_text(block, None, kind, 1)))
                g.multiline |= multi
            for g in groups.values():
                g.short = {t for t in g.tags if t not in g.multiline and all(
                    len(b.values.get(t, "")) <= 30 for _, b, _, _ in split if b.group == g.kind)}
            if kind == "lore":  # 항목이 없는 분류도 제목은 유지
                for t in titles:
                    groups.setdefault(t, BlockGroup(t, t, ["내용"], set(), set(), ""))
                ordered = [groups[t] for t in titles]
            else:
                ordered = list(groups.values())
            parts.append(TemplatePart(top.name, kind, [], ordered, [b for _, b, _, _ in split], notes=part_notes))
            continue
        if sub_part and normalize(top.name) == sub_part and card_part:
            # 조연 카드: 칸 구성은 주연 카드와 같음 (아래에서 채움)
            parts.append(TemplatePart(top.name, "cards", [], [], [], [], f"({top.name} 이름)", part_notes, optional=True))
            continue
        if card_part and normalize(top.name) == card_part and children:
            # 다인챗: 첫 번째 '## 이름' 카드를 카드 한 장의 칸 구성으로 씁니다.
            proto = children[0]
            subs = [s for s in inner if s.level > child_level and proto.start_line <= s.start_line - 1 <= proto.end_line]
            sub_level = min((s.level for s in subs), default=0)
            card_fields = _prose_fields(top.name, proto, [s for s in subs if s.level == sub_level])
            for f in card_fields:
                all_notes += f.notes
            # 큰 항목 메모는 첫 카드 앞에 있는 ※ 줄만 (카드 안 메모는 각 칸이 가짐)
            _, intro_notes = _split_notes(top.body.splitlines()[:proto.start_line - 1 - top.start_line])
            parts.append(TemplatePart(top.name, "cards", [], [], [], card_fields, proto.name, intro_notes))
            continue
        fields = _prose_fields(top.name, top, children)
        for f in fields:
            all_notes += f.notes
        parts.append(TemplatePart(top.name, "prose", fields, [], []))
    main = next((p for p in parts if p.kind == "cards" and not p.optional), None)
    for p in parts:
        if p.optional:
            if main is None:
                p.kind, p.fields = "prose", [ProseField(p.title, "", "", [], p.notes)]
                p.notes = []
            else:
                p.card_fields = main.card_fields
    return TemplateSchema(parts, {l.strip() for l in text.splitlines() if l.strip()},
                          {n.strip() for n in all_notes if n.strip()})


def default_card(part: TemplatePart, name: str | None = None) -> CardData:
    return CardData(part.card_title if name is None else name, {f.title: f.guide for f in part.card_fields})


def default_form(schema: TemplateSchema) -> FormData:
    prose = {f.key: f.guide for p in schema.parts for f in p.fields}
    blocks = {p.title: [TagBlock(b.group, b.name, dict(b.values)) for b in p.defaults]
              for p in schema.parts if p.kind in ("var", "lore")}
    cards = {p.title: [] if p.optional else [default_card(p)] for p in schema.parts if p.kind == "cards"}
    return FormData(prose, blocks, cards)


def group_of(part: TemplatePart, block: TagBlock) -> BlockGroup | None:
    for g in part.groups:
        if g.kind == block.group:
            return g
    return part.groups[0] if part.groups else None


def card_field_label(card: CardData, f: ProseField) -> str:
    return f"{card.label} › {f.title or '머리말'}"


def assemble(schema: TemplateSchema, form: FormData, natural: bool = False) -> tuple[str, list[str]]:
    """양식대로 변환 파일 내용을 만듭니다. (본문, 줄마다 어느 칸에서 왔는지)
    natural=True 면 제목의 '#' 을 빼고 ※ 메모를 넣은 원자료 양식을 만듭니다."""
    out: list[str] = []
    owner: list[str] = []

    def add(line: str, who: str):
        out.append(line)
        owner.append(who)

    def head(level: int, text: str) -> str:
        return text if natural else f"{'#' * level} {text}"

    def add_notes(notes: list[str], who: str):
        if natural:
            for n in notes:
                add(n, who)

    def add_text(text: str, who: str):
        text = text.strip("\n").rstrip()
        for line in text.splitlines() if text else []:
            add(line, who)

    for part in schema.parts:
        if out:
            add("", part.title)
        add(head(1, part.title), part.title)
        add_notes(part.notes, part.title)
        if part.kind == "prose":
            for f in part.fields:
                if f.title:
                    add("", f.label)
                    add(head(2, f.title), f.label)
                add_text(form.prose.get(f.key, ""), f.label)
                add_notes(f.notes, f.label)
            continue
        if part.kind == "cards":
            for card in form.cards.get(part.title, []):
                who = card.label
                add("", who)
                name = card.name.strip() or part.card_title
                add(f"■ {part.title if part.optional else '캐릭터'}: {name}" if natural else f"## {name}", who)
                for f in part.card_fields:
                    label = card_field_label(card, f)
                    if f.title:
                        add("", label)
                        add(head(3, f.title), label)
                    add_text(card.sections.get(f.title, ""), label)
                    add_notes(f.notes, label)
            continue
        number = 0
        blocks = form.blocks.get(part.title, [])
        for g in part.groups:
            add("", part.title)
            add(head(2, g.title), part.title)
            for b in (b for b in blocks if group_of(part, b) is g):
                number += 1
                who = f"변수 '{b.name}'" if part.kind == "var" else f"{part.title} {number}번 '{b.name}'"
                add("", who)
                for line in "\n".join(_block_text(b, g, part.kind, number)).splitlines():
                    add(line, who)
    return "\n".join(out).rstrip() + "\n", owner


SOURCE_FORM_NOTE_CARDS = [
    "※ MELLOY 다인챗 원자료 양식입니다. '설명', '1. 시스템', '신상' 같은 제목 줄은 지우거나 고치지 말고, 각 제목 아래에 내용을 채워 주세요.",
    "※ 등장인물 카드는 '■ 캐릭터: 이름' 줄로 시작합니다. 인물마다 이 줄부터 '고정 규칙'까지를 한 벌씩 복사해서 채워 주세요.",
    "※ 조연이 있으면 '■ 조연: 이름' 줄로 시작해서 주연 카드와 같은 칸으로 채워 주세요. 조연이 없으면 '조연' 제목 아래를 비워 두면 됩니다.",
] + SOURCE_FORM_NOTE[1:]


def build_source_form(schema: TemplateSchema) -> str:
    """제목에 '#' 이 없는 자연어 원자료 양식을 만듭니다."""
    text, _ = assemble(schema, default_form(schema), natural=True)
    lines = [m.group(2).strip() if (m := MD_HEADER.match(l)) else l for l in text.splitlines()]
    note = SOURCE_FORM_NOTE_CARDS if any(p.kind == "cards" for p in schema.parts) else SOURCE_FORM_NOTE
    return "\n".join(note + [""] + lines) + "\n"


def source_form_file() -> Path:
    return SOURCE_FORM_FILE if rule_mode() == "1인" else Path(f"원자료양식_{rule_mode()}.txt")


def _restore_sub_heads(text: str, sub_heads: list[str]) -> str:
    """원자료에서 '#' 없이 쓴 '3-1. 연인 루트' 같은 줄을 템플릿처럼 '### ' 제목으로 되돌립니다."""
    keys = [_compact(re.sub(r"\([^)]*\)", "", h)) for h in sub_heads]
    # '(조직/가문)' 처럼 이름 전체가 빈칸인 제목이 있는 칸은, 글머리표가 아닌 짧은 줄을 제목으로 봅니다.
    free_heads = any(not k for k in keys)
    keys = [k for k in keys if k]
    if not keys and not free_heads:
        return text
    out = []
    for line in text.splitlines():
        s = line.strip()
        if s and not s.startswith(("#",) + BULLETS) and len(s) <= 60:
            if any(_compact(s).startswith(k) for k in keys) or (
                    free_heads and len(s) <= 30 and not s.endswith((".", "다", "요", ":"))):
                line = f"### {s}"
        out.append(line)
    return "\n".join(out)


@dataclass
class FillResult:
    form: FormData
    filled: list[str]  # 채운 칸 이름
    unplaced: list[str]  # 제목을 못 찾아 넣지 못한 줄
    notes: list[str]  # 기타 안내


def fill_from_text(schema: TemplateSchema, text: str, config: dict) -> FillResult:
    """원자료(제목에 '#' 없어도 됨) 또는 변환 파일을 읽어 양식 칸에 채웁니다."""
    form = default_form(schema)
    part_keys: dict[str, TemplatePart] = {}
    for p in schema.parts:
        for alias in _field_aliases(p.title, config):
            part_keys.setdefault(normalize(alias), p)
    cur: TemplatePart | None = None
    skipping = False
    field: ProseField | None = None
    seen: set[str] = set()
    buf: dict[tuple[str, str], list[str]] = {}
    raw: dict[str, list[str]] = {}
    cards: dict[str, list[tuple[str, dict[str, list[str]]]]] = {}  # 큰 항목 → [(카드 이름, {칸: 줄들})]
    card_sub: str | None = None
    unplaced: list[str] = []
    notes: list[str] = []

    for line in text.splitlines():
        if (schema.note_lines and line.strip() in schema.note_lines) or line.strip() in SOURCE_FORM_NOTE_CARDS:
            continue  # 템플릿의 ※ 메모 줄은 내용이 아니므로 건너뜁니다
        hk = _heading_key(line)
        head_m = CARD_HEAD.match(line)
        if head_m:
            # '■ 캐릭터:' 줄은 어디에 있든 카드의 시작이므로 카드 항목으로 넘어갑니다 ('■ 조연:' 은 조연 항목)
            want_sub = head_m.group(1) == "조연"
            card_part = next((p for p in schema.parts if p.kind == "cards" and p.optional == want_sub), None)
            if card_part is not None and card_part is not cur:
                cur, skipping, field, card_sub = card_part, False, None, None
                seen.add(cur.title)
        if hk and hk in part_keys:
            cur = part_keys[hk]
            skipping = cur.title in seen and cur.kind != "cards"
            if skipping:
                notes.append(f"'{cur.title}' 항목이 두 번 이상 나옵니다. 작성 탭은 한 벌만 다루므로 두 번째부터는 넣지 않았습니다.")
            seen.add(cur.title)
            field = next((f for f in cur.fields if not f.title), None)
            card_sub = None
            continue
        if cur is None or skipping:
            if line.strip() and not line.strip().startswith("※"):
                unplaced.append(line)
            continue
        if cur.kind == "cards":
            md = MD_HEADER.match(line)
            nat = CARD_HEAD.match(line)
            sub = next((f for f in cur.card_fields if f.title and hk and normalize(f.title) == hk), None)
            if (md and len(md.group(1)) == 2) or nat:
                cards.setdefault(cur.title, []).append(((nat.group(2) if nat else md.group(2)).strip(), {}))
                card_sub = next((f.title for f in cur.card_fields if not f.title), None)
                continue
            if sub:
                if not cards.get(cur.title):
                    cards.setdefault(cur.title, []).append(("", {}))
                card_sub = sub.title
                cards[cur.title][-1][1].setdefault(card_sub, [])
                continue
            if not cards.get(cur.title) or card_sub is None:
                if line.strip():
                    unplaced.append(line)
                continue
            cards[cur.title][-1][1].setdefault(card_sub, []).append(line)
            continue
        if cur.kind != "prose":
            raw.setdefault(cur.title, []).append(line)
            continue
        match = next((f for f in cur.fields if f.title and hk and normalize(f.title) == hk), None)
        if match:
            field = match
            buf.setdefault(field.key, [])
            continue
        if field is None:
            if line.strip():
                unplaced.append(line)
            continue
        buf.setdefault(field.key, []).append(line)

    filled = []
    for p in schema.parts:
        for f in p.fields:
            if f.key in buf:
                form.prose[f.key] = _restore_sub_heads("\n".join(buf[f.key]).strip("\n").rstrip(), f.sub_heads)
                filled.append(f.label)
        if p.kind in ("var", "lore") and p.title in raw:
            blocks = [b for _, b, _, _ in _split_blocks(raw[p.title], p.kind, [g.title for g in p.groups])]
            if blocks:
                form.blocks[p.title] = blocks
                filled.append(f"{p.title} {len(blocks)}개")
        if p.kind == "cards" and cards.get(p.title):
            parsed = []
            for name, secs in cards[p.title]:
                card = default_card(p, name)
                for f in p.card_fields:
                    if f.title in secs:
                        card.sections[f.title] = _restore_sub_heads("\n".join(secs[f.title]).strip("\n").rstrip(), f.sub_heads)
                parsed.append(card)
            form.cards[p.title] = parsed
            filled.append(f"{p.title} 카드 {len(parsed)}장")
    if notes:
        notes = list(dict.fromkeys(notes))
    return FillResult(form, filled, unplaced, notes)


def _is_placeholder_line(line: str, next_line: str) -> bool:
    """템플릿 안내 문구 중 '채워야 할 빈칸'처럼 보이는 줄인지 판단합니다."""
    s = line.strip().lstrip("#").strip().lstrip("".join(BULLETS)).strip()
    if not s:
        return False
    if s.endswith(":") and next_line.startswith((" ", "\t")):  # 아래에 들여쓴 하위 항목이 있는 제목 줄
        return False
    return bool(s.endswith((":", "=", "!")) or ": /" in s or "= /" in s or s.startswith("(")
                or re.search(r"[:\]]\s*\(", s) or (s.endswith(")") and "(" in s and len(s) <= 40)
                or re.fullmatch(r"\d+\.\s*트리거:\s*!?", s))


def check_form(schema: TemplateSchema, form: FormData, text: str, owner: list[str]) -> list[CheckItem]:
    """작성 탭 전용 점검: 비어 있는 칸, 채우지 않은 안내 문구"""
    items = []
    empty = [f.label for p in schema.parts for f in p.fields if not form.prose.get(f.key, "").strip()]
    for p in schema.parts:
        if p.kind == "cards":
            if not form.cards.get(p.title) and not p.optional:
                items.append(CheckItem(WARN, f"'{p.title}' 카드가 한 장도 없습니다", "카드를 추가해 주세요."))
            for card in form.cards.get(p.title, []):
                empty += [card_field_label(card, f) for f in p.card_fields if not card.sections.get(f.title, "").strip()]
    if empty:
        items.append(CheckItem(WARN, f"비어 있는 칸이 {len(empty)}개 있습니다", ", ".join(empty)))
    lines = text.splitlines()
    left: dict[str, list[str]] = {}
    for i, line in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if line.strip() in schema.lines and _is_placeholder_line(line, nxt):
            left.setdefault(owner[i] if i < len(owner) else "?", []).append(line.strip())
    if left:
        detail = "\n".join(f"· {who}: {rows[0][:30]}" + (f" 외 {len(rows) - 1}줄" if len(rows) > 1 else "")
                           for who, rows in left.items())
        items.append(CheckItem(WARN, f"아직 채우지 않은 템플릿 안내 문구가 {sum(map(len, left.values()))}줄 있습니다", detail))
    return items


def label_line_numbers(detail: str, owner: list[str]) -> str:
    """검사 결과의 'N번째 줄'을 작성 탭의 칸 이름으로 바꿔 보여 줍니다."""
    def repl(m: re.Match) -> str:
        nums = [int(n) for n in re.findall(r"\d+", m.group(1))]
        whos = list(dict.fromkeys(owner[n - 1] for n in nums if 0 < n <= len(owner)))
        return ", ".join(f"'{w}'" for w in whos) + " 칸" if whos else m.group(0)
    return re.sub(r"((?:\d+, )*\d+)번째 줄", repl, detail)


# ── 작성 탭: 이 칸만 AI로 다듬기 ──
REFINE_INSTRUCTION = "표준입력으로 전달된 요청서를 읽고 그 지시에 따라 다듬은 결과 본문만 출력하세요"

REFINE_FORMAT_RULES = {
    "prose": "- 칸 제목(## ...)은 쓰지 말고 이 칸의 본문만 출력한다. 칸 안의 ### 소제목은 그대로 유지한다.",
    "var": ("- [현재 내용]과 같은 형식('[변수 이름] 이름 / 종류' 줄과 '[태그] 내용' 줄)을 그대로 유지해서 변수 블록 하나만 출력한다.\n"
            "- 변수 이름에는 공백을 넣지 않는다.\n"
            "- [변수 내용]과 [값이 변하는 조건]은 공백 포함 {limit}자 이내로 쓴다. [구간별 행동]도 한 줄에 {limit}자를 넘기지 않는다.\n"
            "- {{user}} 대신 '유저'로 쓴다."),
    "lore": ("- [현재 내용]과 같은 형식('N. 트리거: 키워드' 줄과 '[태그] 내용' 줄)을 그대로 유지해서 로어북 항목 하나만 출력한다.\n"
             "- {{user}} 대신 '유저'로 쓴다."),
}


def build_refine_prompt(label: str, kind: str, guide: str, current: str, config: dict,
                        request: str = "", context: str = "") -> str:
    skip = normalize(config.get("템플릿_파일") or TEMPLATE_FILE_DEFAULT)
    rules = [(n, b) for n, b in load_rule_texts() if normalize(n) != skip]
    rule_block = "\n\n".join(f"### 규칙 파일: {n}\n{b.strip()}" for n, b in rules) or "(규칙 파일 없음)"
    limit = str((config.get("변수_검사") or {}).get("변수_내용_최대", 200))
    parts = [
        "너는 캐릭터 설정 작성을 돕는 편집자다. 사용자가 템플릿의 한 칸을 자연어로 채웠다. "
        "이 칸 하나만 템플릿 형식에 맞게 다듬어라.",
        "파일을 읽거나 도구를 쓸 필요는 없다. 아래 내용만으로 작업하라.",
        "", "## 규칙 파일 (rules 폴더)", rule_block,
        "", "## 다듬을 칸", label,
        "", "## 이 칸의 템플릿 안내", "<<<안내 시작>>>", guide.strip() or "(안내 없음)", "<<<안내 끝>>>",
        "", "## 현재 내용", "<<<내용 시작>>>", current.strip(), "<<<내용 끝>>>",
    ]
    if context.strip():
        parts += ["", "## 참고: 문서 전체 (다른 칸과 어긋나지 않는지 확인하는 데만 쓰고, 이 칸 밖의 내용은 출력하지 않는다)",
                  "<<<문서 시작>>>", context.strip(), "<<<문서 끝>>>"]
    parts += [
        "", "## 지시",
        "- 현재 내용을 템플릿 안내의 구조(항목 이름, 글머리표, 순서)에 맞게 정리하고 문장을 다듬는다.",
        "- 사용자가 쓴 설정의 의미·강도·조건·수치·고유명사를 그대로 지킨다. '절대', '반드시' 같은 강도 표현을 약하게 바꾸지 않는다. (기계어치환 원칙)",
        "- 현재 내용에 없는 설정을 지어내지 않는다. 사용자가 채우지 않은 항목은 템플릿 안내 문구를 그대로 남긴다.",
        REFINE_FORMAT_RULES[kind].replace("{limit}", limit),
        "- 설명, 인사말, 코드블록 없이 결과 본문만 출력한다.",
    ]
    if "※" in guide:
        parts.append("- 템플릿 안내 중 ※ 로 시작하는 줄은 작성자용 메모다. 지시로만 참고하고 결과에는 넣지 않는다.")
    if request.strip():
        parts += ["", "## 사용자 요청 (위 지시를 어기지 않는 범위에서 따른다)", request.strip()]
    return "\n".join(parts) + "\n"


def clean_refine_output(text: str, title: str = "") -> str:
    lines = [l for l in text.strip().splitlines() if not l.strip().startswith("```")]
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and title and normalize(lines[0].lstrip("#")) == normalize(title):
        lines.pop(0)
    return "\n".join(lines).strip("\n").rstrip()


# ════════════════════════════════════════════════════════════
#  화면 (UI)
# ════════════════════════════════════════════════════════════
class DropTextEdit(QPlainTextEdit):
    """파일을 끌어다 놓으면(드래그 앤 드롭) 바로 불러옵니다."""

    fileDropped = Signal(str)

    def __init__(self, placeholder: str):
        super().__init__()
        self.setReadOnly(True)
        self.setAcceptDrops(True)
        self.setPlaceholderText(placeholder)
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        urls = e.mimeData().urls()
        if urls and urls[0].isLocalFile():
            self.fileDropped.emit(urls[0].toLocalFile())


class FilePanel:
    """[불러오기] 버튼 + 경로 표시 + 내용 보기 창 한 묶음"""

    def __init__(self, owner: "MainWindow", label: str, placeholder: str):
        self.owner = owner
        self.label = label
        self.real_path: str | None = None  # 다시 읽기용 (화면에는 표시하지 않음)
        self.text = ""

        self.button = QPushButton(f"📂 {label} 불러오기")
        self.button.setMinimumHeight(34)
        self.reload_button = QPushButton("↻ 다시 읽기")
        self.reload_button.setToolTip("메모장 등에서 파일을 고친 뒤 누르면 최신 내용으로 다시 읽습니다.")
        self.reload_button.setEnabled(False)
        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        self.path_edit.setPlaceholderText("아직 불러온 파일이 없습니다")

        self.view = DropTextEdit(f"{placeholder}\n\n위의 버튼을 누르거나, 파일을 이 창으로 끌어다 놓으세요.")
        self.count_label = QLabel("총 0자")
        self.count_label.setStyleSheet("color:#5f6b7a;")

        self.button.clicked.connect(self.choose)
        self.reload_button.clicked.connect(self.reload)
        self.view.fileDropped.connect(self.load)

    def top_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(self.button)
        row.addWidget(self.path_edit, 1)
        row.addWidget(self.reload_button)
        return row

    def box(self) -> QGroupBox:
        box = QGroupBox(self.label)
        lay = QVBoxLayout(box)
        lay.addWidget(self.view, 1)
        lay.addWidget(self.count_label, 0, Qt.AlignRight)
        return box

    def choose(self):
        path, _ = QFileDialog.getOpenFileName(
            self.owner, f"{self.label} 파일 선택", ".",
            "텍스트 파일 (*.txt *.md *.json);;모든 파일 (*.*)")
        if path:
            self.load(path)

    def load(self, path: str):
        try:
            text = read_text_file(path)
        except Exception as e:
            QMessageBox.warning(self.owner, "파일 열기 실패",
                                f"파일을 열지 못했습니다.\n파일: {to_relative(path)}\n\n원인: {e}")
            return
        self.set_text(text, to_relative(path))
        self.real_path = path
        self.reload_button.setEnabled(True)
        self.owner.statusBar().showMessage(f"{self.label} 파일을 불러왔습니다: {to_relative(path)}", 5000)

    def set_text(self, text: str, display: str):
        """파일 없이 내용을 바로 넣습니다. (작성 탭에서 보낼 때)"""
        self.real_path = None
        self.text = text
        self.path_edit.setText(display)
        self.view.setPlainText(text)
        self.count_label.setText(f"총 {ulen(text):,}자 · {text.count(chr(10)) + 1:,}줄")
        self.reload_button.setEnabled(False)

    def reload(self):
        if self.real_path:
            self.load(self.real_path)


class RefineDialog(QDialog):
    """AI 가 다듬은 결과를 현재 내용과 나란히 보여 주고, 적용할지 고릅니다."""

    def __init__(self, parent: QWidget, title: str, before: str, after: str):
        super().__init__(parent)
        self.setWindowTitle(f"AI 다듬기 결과 · {title}")
        self.resize(1100, 640)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("오른쪽 AI 제안은 여기서 바로 고친 뒤 적용할 수 있습니다. "
                             "의미가 바뀌거나 없는 설정이 추가되지 않았는지 꼭 확인해 주세요."))
        split = QSplitter(Qt.Horizontal)
        for caption, text, editable in (("현재 내용", before, False), ("AI 제안", after, True)):
            box = QGroupBox(caption)
            bl = QVBoxLayout(box)
            edit = QPlainTextEdit(text)
            edit.setReadOnly(not editable)
            bl.addWidget(edit)
            split.addWidget(box)
            if editable:
                self.after_edit = edit
        lay.addWidget(split, 1)
        buttons = QDialogButtonBox()
        buttons.addButton("✔ 적용", QDialogButtonBox.AcceptRole)
        buttons.addButton("취소", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def result_text(self) -> str:
        return self.after_edit.toPlainText()


def _small_edit(lines: int) -> QPlainTextEdit:
    edit = QPlainTextEdit()
    edit.setTabChangesFocus(True)
    edit.setFixedHeight(edit.fontMetrics().lineSpacing() * lines + 14)
    return edit


class BlockEditor(QFrame):
    """변수 블록 / 로어북 항목 하나를 고치는 칸"""

    def __init__(self, writer: "WriterTab", part: TemplatePart, block: TagBlock):
        super().__init__()
        self.writer, self.part, self.block = writer, part, block
        self.setFrameShape(QFrame.StyledPanel)
        group = group_of(part, block)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)

        head = QHBoxLayout()
        name = QLineEdit(block.name)
        if part.kind == "var":
            head.addWidget(QLabel("변수 이름"))
            name.setPlaceholderText("공백 없이 (예: 최근사건)")
            head.addWidget(name, 1)
            kind = QComboBox()
            kinds = [g.kind for g in part.groups]
            kind.addItems(kinds + ([block.group] if block.group not in kinds else []))
            kind.setCurrentText(block.group)
            kind.currentTextChanged.connect(self._kind_changed)
            head.addWidget(QLabel("종류"))
            head.addWidget(kind)
        else:
            head.addWidget(QLabel("트리거"))
            name.setPlaceholderText("예: !요약")
            head.addWidget(name, 1)
        name.textChanged.connect(self._name_changed)
        ai = QPushButton("✨ AI로 다듬기")
        ai.clicked.connect(lambda: writer.refine_block(part, block))
        delete = QPushButton("🗑 삭제")
        delete.clicked.connect(lambda: writer.delete_block(part, block))
        head.addWidget(ai)
        head.addWidget(delete)
        writer.block_ai_buttons.setdefault(part.title, []).append(ai)
        lay.addLayout(head)

        limit = int((writer.config.get("변수_검사") or {}).get("변수_내용_최대", 200))
        tags = list(group.tags) if group else []
        tags += [t for t in block.values if t not in tags]
        for tag in tags:
            row = QHBoxLayout()
            label = QLabel(f"[{tag}]")
            label.setFixedWidth(130)
            label.setAlignment(Qt.AlignRight | Qt.AlignTop)
            row.addWidget(label)
            value = block.values.get(tag, "")
            if group and tag in group.short and "\n" not in value:
                edit = QLineEdit(value)
                edit.textChanged.connect(lambda t, tag=tag: self._value_changed(tag, t))
                row.addWidget(edit, 1)
            else:
                edit = _small_edit(5 if group and tag in group.multiline else 3)
                edit.setPlainText(value)
                count = QLabel()
                count.setFixedWidth(70)
                count.setAlignment(Qt.AlignRight | Qt.AlignTop)

                def update(edit=edit, count=count, tag=tag):
                    text = edit.toPlainText()
                    self._value_changed(tag, text)
                    rows = text.splitlines() or [""]
                    n = ulen(text.strip()) if tag != "구간별 행동" else max(ulen(r.strip()) for r in rows)
                    over = n > limit and part.kind == "var"
                    count.setText(f"{'한 줄 최대 ' if tag == '구간별 행동' else ''}{n}자")
                    count.setStyleSheet(f"color:{STATUS_STYLE[FAIL][1] if over else '#5f6b7a'};")

                edit.textChanged.connect(update)
                update()
                row.addWidget(edit, 1)
                row.addWidget(count)
            lay.addLayout(row)

    def _name_changed(self, text: str):
        self.block.name = text.strip()
        self.writer.changed()

    def _value_changed(self, tag: str, text: str):
        if self.block.values.get(tag, "") != text:
            self.block.values[tag] = text
            self.writer.changed()

    def _kind_changed(self, kind: str):
        self.block.group = kind
        self.writer.changed()
        QTimer.singleShot(0, lambda: self.writer.rebuild_block_page(self.part))


class WriterTab(QWidget):
    """[작성] 탭: 템플릿 칸을 자연어로 채우면 템플릿 양식의 변환 파일을 만들어 줍니다."""

    def __init__(self, main: "MainWindow"):
        super().__init__()
        self.main = main
        self.config, _ = load_config()
        self.schema: TemplateSchema | None = None
        self.form: FormData | None = None
        self.save_path: str | None = None  # 실제 저장 위치 (화면에는 상대경로만 표시)
        self.source_text: str | None = None  # 불러온 원자료 (검수 탭의 원본으로 보냄)
        self.source_label = ""
        self.dirty = False
        self.loading = False
        self.text, self.owner = "", []
        self.prose_edits: dict[tuple[str, str], QPlainTextEdit] = {}
        self.block_pages: dict[str, int] = {}
        self.prose_ai_buttons: list[QPushButton] = []
        self.block_ai_buttons: dict[str, list[QPushButton]] = {}  # 큰 항목 제목 → 버튼들
        self.card_items: dict[int, QTreeWidgetItem] = {}  # 카드 uid → 목록 항목
        self.card_count_labels: dict[int, QLabel] = {}

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(400)
        self.timer.timeout.connect(self.refresh)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 6)

        bar = QHBoxLayout()
        buttons = [
            ("📝 새로 작성", "템플릿의 안내 문구가 채워진 빈 양식으로 시작합니다.", self.new_doc),
            ("📂 불러오기", "원자료(제목에 '#' 없어도 됨)나 저장해 둔 변환 파일을 불러와 칸에 채웁니다.", self.open_doc),
            ("💾 저장", "변환 파일로 저장합니다. (처음에는 생성결과 폴더에 저장)", self.save_doc),
            ("💾 다른 이름으로", "다른 이름으로 저장합니다.", self.save_doc_as),
            ("🔍 검수 탭으로 보내기", "지금 내용을 검수 탭의 생성 결과로 보내고 기계 검사를 실행합니다.", self.send_to_review),
            ("📄 원자료 양식 만들기", "제목에 '#' 이 없는 자연어 원자료 양식(원자료양식.txt)을 만듭니다.", self.make_source_form),
        ]
        for text, tip, slot in buttons:
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setMinimumHeight(32)
            b.clicked.connect(slot)
            bar.addWidget(b)
        bar.addStretch(1)
        self.file_label = QLabel()
        bar.addWidget(self.file_label)
        lay.addLayout(bar)

        ai_bar = QHBoxLayout()
        ai_bar.addWidget(QLabel("✨ AI 다듬기 요청 (선택)"))
        self.ai_request = QLineEdit()
        self.ai_request.setPlaceholderText("예: 말투 예시를 조금 더 구체적으로 / 비워 두면 형식만 맞춥니다")
        ai_bar.addWidget(self.ai_request, 1)
        self.ai_context = QCheckBox("문서 전체도 참고 (사용량 늘어남)")
        self.ai_context.setToolTip("다른 칸과 어긋나지 않게 문서 전체를 함께 보냅니다. Claude 사용량이 크게 늘어납니다.")
        ai_bar.addWidget(self.ai_context)
        self.ai_stop = QPushButton("⏹ AI 중지")
        self.ai_stop.setVisible(False)
        self.ai_stop.clicked.connect(lambda: self.main.worker and self.main.worker.cancel())
        ai_bar.addWidget(self.ai_stop)
        lay.addLayout(ai_bar)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["항목", "글자 수"])
        self.tree.setColumnWidth(0, 210)
        self.tree.currentItemChanged.connect(self._tree_selected)
        self.stack = QStackedWidget()
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.tree)
        split.addWidget(self.stack)
        split.setSizes([300, 980])

        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        self.counts = QLabel()
        self.counts.setWordWrap(True)
        bl.addWidget(self.counts)
        self.issues = QTextBrowser()
        bl.addWidget(self.issues)

        vsplit = QSplitter(Qt.Vertical)
        vsplit.addWidget(split)
        vsplit.addWidget(bottom)
        vsplit.setSizes([620, 200])
        lay.addWidget(vsplit, 1)

        self.new_doc(ask=False)

    # ── 화면 만들기 ──
    def load_form(self, schema: TemplateSchema, form: FormData):
        self.loading = True
        self.schema, self.form = schema, form
        self.prose_edits.clear()
        self.block_pages.clear()
        self.card_items.clear()
        self.card_count_labels.clear()
        self.prose_ai_buttons, self.block_ai_buttons = [], {}
        self.tree.clear()
        while self.stack.count():
            w = self.stack.widget(0)
            self.stack.removeWidget(w)
            w.deleteLater()
        first = None
        for part in schema.parts:
            top = QTreeWidgetItem([part.title, ""])
            self.tree.addTopLevelItem(top)
            if part.kind == "prose":
                for f in part.fields:
                    idx = self.stack.addWidget(self._prose_page(part, f))
                    item = QTreeWidgetItem([f.label, ""])
                    item.setData(0, Qt.UserRole, idx)
                    top.addChild(item)
                    if first is None:
                        first = item
                top.setData(0, Qt.UserRole, top.child(0).data(0, Qt.UserRole) if top.childCount() else None)
            elif part.kind == "cards":
                idx = self.stack.addWidget(self._cards_page(part))
                top.setData(0, Qt.UserRole, idx)
                for card in self.form.cards.setdefault(part.title, []):
                    citem = QTreeWidgetItem([card.label, ""])
                    top.addChild(citem)
                    self.card_items[card.uid] = citem
                    for f in part.card_fields:
                        sidx = self.stack.addWidget(self._card_field_page(part, card, f))
                        sitem = QTreeWidgetItem([f.title or "머리말", ""])
                        sitem.setData(0, Qt.UserRole, sidx)
                        citem.addChild(sitem)
                    citem.setData(0, Qt.UserRole, citem.child(0).data(0, Qt.UserRole) if citem.childCount() else idx)
                if first is None:
                    first = top
            else:
                idx = self.stack.addWidget(QWidget())
                self.block_pages[part.title] = idx
                top.setData(0, Qt.UserRole, idx)
                self.rebuild_block_page(part)
                if first is None:
                    first = top
            top.setExpanded(True)
        self.loading = False
        if first:
            self.tree.setCurrentItem(first)
        self.refresh()

    def _prose_page(self, part: TemplatePart, f: ProseField) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        title = QLabel(f"<b>{html.escape(part.title)} › {html.escape(f.label)}</b>")
        head.addWidget(title)
        head.addStretch(1)
        guide = QPushButton("📋 템플릿 안내 보기")
        guide.clicked.connect(lambda: self._show_guide(f))
        ai = QPushButton("✨ 이 칸 AI로 다듬기")
        ai.setStyleSheet("font-weight:bold;")
        ai.clicked.connect(lambda: self.refine_prose(part, f))
        self.prose_ai_buttons.append(ai)
        head.addWidget(guide)
        head.addWidget(ai)
        lay.addLayout(head)
        edit = QPlainTextEdit()
        edit.setPlainText(self.form.prose.get(f.key, ""))
        edit.textChanged.connect(lambda: self._prose_changed(f, edit))
        lay.addWidget(edit, 1)
        self.prose_edits[f.key] = edit
        return page

    def _cards_page(self, part: TemplatePart) -> QWidget:
        """카드 목록: 이름 바꾸기 / 순서 / 지우기 / 추가"""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.addWidget(QLabel(f"<b>{html.escape(part.title)}</b> — 인물마다 카드 한 장. 카드 이름은 '## 이름' 제목이 되고, "
                             "칸 구성(" + " · ".join(f.title for f in part.card_fields if f.title) + ")은 모든 카드에 똑같이 붙습니다."))
        if part.notes:
            note = QLabel("\n".join(n.strip() for n in part.notes))
            note.setWordWrap(True)
            note.setStyleSheet("color:#5f6b7a;")
            lay.addWidget(note)
        cards = self.form.cards.setdefault(part.title, [])
        for i, card in enumerate(cards):
            row = QHBoxLayout()
            name = QLineEdit(card.name)
            name.setPlaceholderText("이름 (한자 / 로마자)")
            name.textChanged.connect(lambda t, c=card: self._rename_card(c, t))
            count = QLabel()
            count.setFixedWidth(110)
            self.card_count_labels[card.uid] = count
            up, down, delete = QPushButton("▲"), QPushButton("▼"), QPushButton("🗑 지우기")
            up.setEnabled(i > 0)
            down.setEnabled(i < len(cards) - 1)
            up.clicked.connect(lambda _=False, c=card: self._move_card(part, c, -1))
            down.clicked.connect(lambda _=False, c=card: self._move_card(part, c, 1))
            delete.clicked.connect(lambda _=False, c=card: self._delete_card(part, c))
            row.addWidget(QLabel(f"{i + 1}."))
            row.addWidget(name, 1)
            row.addWidget(count)
            for b in (up, down, delete):
                row.addWidget(b)
            lay.addLayout(row)
        add = QPushButton("+ 카드 추가")
        add.clicked.connect(lambda: self._add_card(part))
        lay.addWidget(add, 0, Qt.AlignLeft)
        lay.addStretch(1)
        scroll.setWidget(inner)
        return scroll

    def _card_field_page(self, part: TemplatePart, card: CardData, f: ProseField) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        title = QLabel()
        title.setText(f"<b>{html.escape(part.title)} › {html.escape(card.label)} › {html.escape(f.title or '머리말')}</b>")
        head.addWidget(title)
        head.addStretch(1)
        guide = QPushButton("📋 템플릿 안내 보기")
        guide.clicked.connect(lambda: self._show_guide(f))
        ai = QPushButton("✨ 이 칸 AI로 다듬기")
        ai.setStyleSheet("font-weight:bold;")
        ai.clicked.connect(lambda: self.refine_card(part, card, f))
        self.prose_ai_buttons.append(ai)
        head.addWidget(guide)
        head.addWidget(ai)
        lay.addLayout(head)
        edit = QPlainTextEdit()
        edit.setPlainText(card.sections.get(f.title, ""))

        def on_change():
            card.sections[f.title] = edit.toPlainText()
            self.changed()

        edit.textChanged.connect(on_change)
        lay.addWidget(edit, 1)
        self.prose_edits[("card", card.uid, f.title)] = edit
        return page

    def _select(self, match) -> None:
        """조건에 맞는 목록 항목을 고릅니다."""
        it = QTreeWidgetItemIterator(self.tree)
        while it.value():
            if match(it.value()):
                self.tree.setCurrentItem(it.value())
                return
            it += 1

    def _reload_cards(self, part: TemplatePart):
        """카드 구성이 바뀌면 목록과 화면을 다시 만들고 카드 목록 화면으로 돌아갑니다."""
        self.load_form(self.schema, self.form)
        self._select(lambda item: item.parent() is None and item.text(0) == part.title)

    def _rename_card(self, card: CardData, text: str):
        card.name = text.strip()
        item = self.card_items.get(card.uid)
        if item is not None:
            item.setText(0, card.label)
        self.changed()

    def _add_card(self, part: TemplatePart):
        self.form.cards.setdefault(part.title, []).append(default_card(part))
        self.changed()
        self._reload_cards(part)

    def _move_card(self, part: TemplatePart, card: CardData, step: int):
        cards = self.form.cards[part.title]
        i = cards.index(card)
        j = i + step
        if 0 <= j < len(cards):
            cards[i], cards[j] = cards[j], cards[i]
            self.changed()
            self._reload_cards(part)

    def _delete_card(self, part: TemplatePart, card: CardData):
        if QMessageBox.question(self, "삭제 확인", f"'{card.label}' 카드를 지울까요? 카드 안의 내용도 함께 지워집니다.") != QMessageBox.Yes:
            return
        self.form.cards[part.title].remove(card)
        self.changed()
        self._reload_cards(part)

    def rebuild_block_page(self, part: TemplatePart):
        idx = self.block_pages.get(part.title)
        if idx is None:
            return
        self.block_ai_buttons[part.title] = []
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.addWidget(QLabel(f"<b>{html.escape(part.title)}</b> — 템플릿 형식([변수 이름], [태그], 'N. 트리거:')은 저장할 때 자동으로 붙습니다."))
        blocks = self.form.blocks.setdefault(part.title, [])
        for g in part.groups:
            box = QGroupBox(g.title)
            bl = QVBoxLayout(box)
            for b in (b for b in blocks if group_of(part, b) is g):
                bl.addWidget(BlockEditor(self, part, b))
            add = QPushButton(f"+ {g.title} 추가")
            add.clicked.connect(lambda _=False, g=g: self.add_block(part, g))
            bl.addWidget(add, 0, Qt.AlignLeft)
            lay.addWidget(box)
        lay.addStretch(1)
        scroll.setWidget(inner)
        old = self.stack.widget(idx)
        current = self.stack.currentIndex()
        self.stack.insertWidget(idx, scroll)
        self.stack.removeWidget(old)
        old.deleteLater()
        self.stack.setCurrentIndex(current)
        self.set_ai_running(self.main.ai_busy())

    def _tree_selected(self, item: QTreeWidgetItem, _prev=None):
        if item is not None and item.data(0, Qt.UserRole) is not None:
            self.stack.setCurrentIndex(item.data(0, Qt.UserRole))

    def _show_guide(self, f: ProseField):
        box = QMessageBox(self)
        box.setWindowTitle(f"템플릿 안내 · {f.label}")
        box.setText(f"'{f.label}' 칸의 템플릿 안내입니다. 아래 'Show Details...'에서 복사할 수 있습니다.")
        box.setDetailedText("\n\n".join(x for x in (f.guide, "\n".join(n.strip() for n in f.notes)) if x) or "(안내 없음)")
        box.exec()

    # ── 내용 변경 ──
    def _prose_changed(self, f: ProseField, edit: QPlainTextEdit):
        self.form.prose[f.key] = edit.toPlainText()
        self.changed()

    def changed(self):
        if self.loading:
            return
        self.dirty = True
        self.timer.start()

    def add_block(self, part: TemplatePart, g: BlockGroup):
        blocks = self.form.blocks.setdefault(part.title, [])
        new = TagBlock(g.kind, "", {t: "" for t in g.tags})
        last = max((i for i, b in enumerate(blocks) if group_of(part, b) is g), default=len(blocks) - 1)
        blocks.insert(last + 1, new)
        self.changed()
        self.rebuild_block_page(part)

    def delete_block(self, part: TemplatePart, block: TagBlock):
        name = block.name or "(이름 없음)"
        if QMessageBox.question(self, "삭제 확인", f"'{name}' 항목을 삭제할까요?") != QMessageBox.Yes:
            return
        self.form.blocks[part.title].remove(block)
        self.changed()
        self.rebuild_block_page(part)

    # ── 점검 ──
    def refresh(self):
        if not self.schema:
            return
        self.config, _ = load_config()
        self.text, self.owner = assemble(self.schema, self.form)
        self._update_tree_counts()
        self._update_counts()
        self._update_issues()
        self._update_file_label()

    def _update_tree_counts(self):
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            part = self.schema.part(top.text(0))
            if part is None:
                continue
            if part.kind == "prose":
                for j, f in enumerate(part.fields):
                    if j < top.childCount():
                        top.child(j).setText(1, f"{ulen(self.form.prose.get(f.key, '').strip()):,}")
            elif part.kind == "cards":
                cards = self.form.cards.get(part.title, [])
                sizes = [count_chars(c.body, True) for c in _card_sections(parse_md_sections(self.text), self.config, part.title)[1]]
                limit = int((self.config.get("카드_글자수") or {}).get("최대", 5000))
                top.setText(1, f"{len(cards)}장")
                for j, card in enumerate(cards):
                    n = sizes[j] if j < len(sizes) else 0
                    item = self.card_items.get(card.uid)
                    if item is not None:
                        item.setText(1, f"{n:,}")
                        for k, f in enumerate(part.card_fields):
                            if k < item.childCount():
                                item.child(k).setText(1, f"{len(card.sections.get(f.title, '').strip()):,}")
                    label = self.card_count_labels.get(card.uid)
                    if label is not None:
                        label.setText(f"{n:,} / {limit:,}자")
                        label.setStyleSheet(f"color:{STATUS_STYLE[FAIL][1] if n > limit else '#5f6b7a'};")
            else:
                top.setText(1, f"{len(self.form.blocks.get(part.title, []))}개")

    def _update_counts(self):
        secs = parse_md_sections(self.text)
        parts = []
        for rule in self.config.get("글자수_제한", []):
            field, limit = rule.get("필드", "?"), int(rule.get("최대", 0))
            found = _matching_sections(secs, [field] + list(rule.get("별칭", [])))
            n = sum(count_chars(s.body, True) for s in found)
            rec, rec_name = recommended_line(rule, self.config)
            color = STATUS_STYLE[FAIL][1] if n > limit else STATUS_STYLE[WARN][1] if n > rec else "#334155"
            parts.append(f"<span style='color:{color}'>{html.escape(field)} <b>{n:,}</b> / {limit:,}"
                         f" <small>({rec_name} {rec:,})</small></span>")
        if self.config.get("카드_항목"):
            rule = dict(self.config.get("카드_글자수") or {})
            rule.setdefault("최대", 5000)
            limit = int(rule["최대"])
            goal, goal_name = recommended_line(rule, self.config)
            sizes = [count_chars(c.body, True) for c in _card_sections(secs, self.config)[1]]
            if self.config.get("조연_항목"):
                sizes += [count_chars(c.body, True) for c in _card_sections(secs, self.config, self.config["조연_항목"])[1]]
            top = max(sizes, default=0)
            color = STATUS_STYLE[FAIL][1] if top > limit else STATUS_STYLE[WARN][1] if top > goal else "#334155"
            parts.append(f"<span style='color:{color}'>카드 {len(sizes)}장 · 가장 긴 카드 <b>{top:,}</b> / {limit:,}"
                         f" ({goal_name} {goal:,})</span>")
        size_line = ""
        if isinstance(self.config.get("합계_제한"), dict):
            item = _check_total_length(secs, self.config)[0]
            color = STATUS_STYLE[item.status][1] if item.status != PASS else "#334155"
            sizes, total = setting_size(secs, self.config)
            rule = self.config["합계_제한"]
            breakdown = " + ".join(f"{html.escape(p.label)}" + (f"(턴당 최대 {p.note[6:]})" if p.note.startswith("턴당 최대") else "")
                                   + f" {p.used:,}" for p in sizes)
            tokens = token_text(total, self.config)
            size_line = (f"<br><span style='color:{color}'>설정 크기 <b>{total:,}자</b>"
                         + (f" ≈ <b>{html.escape(tokens)}</b>" if tokens else "")
                         + f" (권장 {int(rule.get('권장', 0)):,} / 최대 {int(rule.get('최대', 0)):,})</span>"
                         f" <span style='color:#5f6b7a'>= {breakdown}</span>")
        self.counts.setText(" &nbsp;·&nbsp; ".join(parts) + size_line)

    def _update_issues(self):
        items = [it for _, group in run_mechanical_checks("", self.text, self.config) for it in group
                 if it.status in (WARN, FAIL)]
        items += check_form(self.schema, self.form, self.text, self.owner)
        for it in items:
            it.detail = label_line_numbers(it.detail, self.owner)
        if not items:
            self.issues.setHtml(f"<p style='color:{STATUS_STYLE[PASS][1]}'>✔ 기계 점검에서 걸린 것이 없습니다. "
                                "저장한 뒤 검수 탭에서 원자료와 비교해 보세요.</p>")
            return
        items.sort(key=lambda it: it.status != FAIL)
        self.issues.setHtml("<p style='margin:0 0 4px 0;'><b>실시간 점검</b> (쓰는 동안 자동으로 갱신됩니다)</p>"
                            + "".join(MainWindow._item_html(it) for it in items))

    def _update_file_label(self):
        where = to_relative(self.save_path) if self.save_path else "아직 저장 안 함"
        self.file_label.setText(f"{'● ' if self.dirty else ''}{where}")
        self.file_label.setStyleSheet(f"color:{STATUS_STYLE[WARN][1] if self.dirty else '#5f6b7a'};")

    # ── 파일 ──
    def _confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        return QMessageBox.question(self, "확인", "저장하지 않은 작성 내용이 있습니다. 계속하면 사라집니다. 계속할까요?") == QMessageBox.Yes

    def _load_schema(self) -> TemplateSchema | None:
        self.config, _ = load_config()
        schema, err = load_template_schema(self.config)
        if schema is None:
            QMessageBox.warning(self, "템플릿 오류", err)
        return schema

    def new_doc(self, ask: bool = True):
        if ask and not self._confirm_discard():
            return
        schema = self._load_schema()
        if schema is None:
            return
        self.save_path, self.source_text, self.source_label = None, None, ""
        self.load_form(schema, default_form(schema))
        self.dirty = False
        self._update_file_label()

    def open_doc(self):
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "원자료 또는 변환 파일 불러오기", ".",
                                              "텍스트 파일 (*.txt *.md);;모든 파일 (*.*)")
        if not path:
            return
        try:
            text = read_text_file(path)
        except Exception as e:
            QMessageBox.warning(self, "파일 열기 실패", f"파일을 열지 못했습니다.\n파일: {to_relative(path)}\n\n원인: {e}")
            return
        schema = self._load_schema()
        if schema is None:
            return
        r = fill_from_text(schema, text, self.config)
        if not r.filled:
            QMessageBox.warning(self, "불러오기 실패",
                                "템플릿의 제목(등장인물, 0. Core 등)을 하나도 찾지 못했습니다.\n"
                                "원자료의 제목 줄이 템플릿과 같은지 확인해 주세요. '원자료 양식 만들기'로 양식을 받을 수 있습니다.")
            return
        converted = any(MD_HEADER.match(l) for l in text.splitlines())
        # 원자료(제목에 '#' 없음)를 열었으면 원자료 파일을 덮어쓰지 않도록 새 이름으로 저장하게 합니다.
        self.save_path = path if converted else None
        self.source_text, self.source_label = (None, "") if converted else (text, to_relative(path))
        self.load_form(schema, r.form)
        self.dirty = not converted
        self._update_file_label()
        msg = [f"{len(r.filled)}개 칸을 채웠습니다."]
        missing = [f.label for p in schema.parts for f in p.fields if f.label not in r.filled]
        if missing:
            msg.append(f"원자료에 없어서 템플릿 안내 문구로 남긴 칸: {', '.join(missing)}")
        msg += r.notes
        box = QMessageBox(QMessageBox.Information, "불러오기 완료", "\n\n".join(msg), parent=self)
        if r.unplaced:
            box.setInformativeText(f"제목 아래에 있지 않아 넣지 못한 줄이 {len(r.unplaced)}줄 있습니다. 'Show Details...'에서 확인하세요.")
            box.setDetailedText("\n".join(r.unplaced))
        box.exec()

    def save_doc(self) -> bool:
        if not self.save_path:
            return self.save_doc_as()
        return self._write(self.save_path)

    def save_doc_as(self) -> bool:
        OUTPUT_DIR.mkdir(exist_ok=True)
        prefix = "변환" if rule_mode() == "1인" else f"{rule_mode()}_변환"
        default = OUTPUT_DIR / f"{prefix}_{datetime.now():%Y%m%d_%H%M}.md"
        path, _ = QFileDialog.getSaveFileName(self, "변환 파일 저장", str(default), "마크다운 (*.md);;텍스트 (*.txt)")
        if not path:
            return False
        return self._write(path)

    def _write(self, path: str) -> bool:
        self.refresh()
        try:
            Path(path).write_text(self.text, encoding="utf-8")
        except Exception as e:
            QMessageBox.warning(self, "저장 실패", f"저장하지 못했습니다.\n{e}")
            return False
        self.save_path, self.dirty = path, False
        self._update_file_label()
        self.main.statusBar().showMessage(f"저장했습니다: {to_relative(path)}", 5000)
        return True

    def send_to_review(self):
        self.refresh()
        label = to_relative(self.save_path) if self.save_path and not self.dirty else "(작성 탭 내용 · 저장 전)"
        self.main.result.set_text(self.text, label)
        if self.source_text is not None:
            self.main.original.set_text(self.source_text, self.source_label)
        self.main.tabs.setCurrentWidget(self.main.review_tab)
        self.main.run_mechanical()

    def make_source_form(self):
        schema = self._load_schema()
        if schema is None:
            return
        form_file = source_form_file()
        if form_file.exists() and QMessageBox.question(
                self, "덮어쓰기 확인", f"{form_file} 이(가) 이미 있습니다. 지금 템플릿으로 다시 만들까요?") != QMessageBox.Yes:
            return
        try:
            form_file.write_text(build_source_form(schema), encoding="utf-8")
        except Exception as e:
            QMessageBox.warning(self, "저장 실패", f"원자료 양식을 저장하지 못했습니다.\n{e}")
            return
        QMessageBox.information(self, "원자료 양식",
                                f"{form_file} 을(를) 만들었습니다.\n\n"
                                "AI 어시스턴트에게 이 양식을 주고 '이 제목 순서대로 원자료를 써 줘'라고 하면, "
                                "결과를 [불러오기]로 바로 칸에 채울 수 있습니다.\n"
                                f"{rules_dir()}/템플릿.md 를 고쳤다면 이 버튼으로 양식을 다시 만들어 주세요.")
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(form_file.resolve())))

    # ── AI 다듬기 ──
    def set_ai_running(self, running: bool):
        self.ai_stop.setVisible(running)
        for b in self.prose_ai_buttons + [b for bs in self.block_ai_buttons.values() for b in bs]:
            b.setEnabled(not running)

    def _ai_ready(self) -> bool:
        if self.main.ai_busy():
            QMessageBox.information(self, "안내", "다른 AI 작업이 진행 중입니다. 끝난 뒤에 다시 눌러 주세요.")
            return False
        if not find_claude():
            QMessageBox.warning(self, "Claude Code 없음", CLAUDE_MISSING_MSG)
            return False
        return True

    def _context(self) -> str:
        if not self.ai_context.isChecked():
            return ""
        self.refresh()
        return self.text

    def refine_prose(self, part: TemplatePart, f: ProseField):
        self._refine_text(f.key, f"{part.title} > {f.label}", f, f.label)

    def refine_card(self, part: TemplatePart, card: CardData, f: ProseField):
        self._refine_text(("card", card.uid, f.title), f"{part.title} > {card.label} > {f.title or '머리말'}", f,
                          f"{card.label} › {f.title or '머리말'}")

    def _refine_text(self, key, label: str, f: ProseField, short: str):
        edit = self.prose_edits.get(key)
        if edit is None or not self._ai_ready():
            return
        before = edit.toPlainText()
        if not before.strip():
            QMessageBox.information(self, "안내", "칸에 내용을 먼저 써 주세요.")
            return
        guide = "\n".join(x for x in (f.guide, "\n".join(n.strip() for n in f.notes)) if x)
        prompt = build_refine_prompt(label, "prose", guide, before, self.config, self.ai_request.text(), self._context())

        def done(ok: bool, out: str):
            if not ok:
                if not self.main.ai_cancelled:
                    QMessageBox.warning(self, "AI 다듬기 실패", out)
                return
            if self.prose_edits.get(key) is not edit:
                QMessageBox.information(self, "안내", "AI가 작업하는 동안 다른 문서를 열어서 결과를 적용하지 않았습니다.")
                return
            after = clean_refine_output(out, f.title)
            notes = {n.strip() for n in f.notes}
            if notes:  # 템플릿 ※ 메모가 결과에 섞여 들어오면 뺍니다
                after = "\n".join(l for l in after.splitlines() if l.strip() not in notes)
            if edit.toPlainText() != before and QMessageBox.question(
                    self, "확인", "AI가 작업하는 동안 칸 내용이 바뀌었습니다. 그래도 AI 결과를 볼까요?") != QMessageBox.Yes:
                return
            dlg = RefineDialog(self, short, edit.toPlainText(), after)
            if dlg.exec() == QDialog.Accepted:
                cursor = edit.textCursor()  # Ctrl+Z 로 되돌릴 수 있게 커서로 바꿉니다
                cursor.select(QTextCursor.Document)
                cursor.insertText(dlg.result_text())

        self.main.start_ai(prompt, REFINE_INSTRUCTION, done, f"'{short}' 칸 AI 다듬기")

    def refine_block(self, part: TemplatePart, block: TagBlock):
        if not self._ai_ready():
            return
        g = group_of(part, block)
        blocks = self.form.blocks.get(part.title, [])
        number = blocks.index(block) + 1 if block in blocks else 1
        before = "\n".join(_block_text(block, g, part.kind, number))
        label = f"{part.title} > {block.name or '(이름 없음)'}"
        prompt = build_refine_prompt(label, part.kind, g.sample if g else "", before, self.config,
                                     self.ai_request.text(), self._context())

        def done(ok: bool, out: str):
            if not ok:
                if not self.main.ai_cancelled:
                    QMessageBox.warning(self, "AI 다듬기 실패", out)
                return
            if block not in self.form.blocks.get(part.title, []):
                QMessageBox.information(self, "안내", "AI가 작업하는 동안 이 항목이 삭제되어 결과를 적용하지 않았습니다.")
                return
            dlg = RefineDialog(self, block.name or part.title, before, clean_refine_output(out))
            while dlg.exec() == QDialog.Accepted:
                parsed = _split_blocks(dlg.result_text().splitlines(), part.kind, [x.title for x in part.groups])
                if not parsed:
                    QMessageBox.warning(self, "형식 오류", "결과에서 항목을 찾지 못했습니다. "
                                        + ("'[변수 이름] 이름 / 종류' 줄이" if part.kind == "var" else "'N. 트리거:' 줄이")
                                        + " 있는지 확인해 주세요.")
                    continue
                new = parsed[0][1]
                block.name, block.values = new.name, new.values
                if part.kind == "var" and new.group:
                    block.group = new.group
                self.changed()
                self.rebuild_block_page(part)
                break

        self.main.start_ai(prompt, REFINE_INSTRUCTION, done, f"'{block.name or part.title}' AI 다듬기")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        try:  # 마지막으로 쓴 규칙 모드(1인 / 다인)
            set_rule_mode(read_text_file(MODE_FILE).strip() if MODE_FILE.exists() else "1인")
        except Exception:
            set_rule_mode("1인")
        self._update_title()
        self.resize(1280, 860)

        self.config, config_error = load_config()
        self.mech_groups: list[tuple[str, list[CheckItem]]] = []
        self.ai_items: list[CheckItem] = []
        self.ai_note = ""
        self.worker: AiWorker | None = None
        self.ai_label = ""
        self.ai_callback = None
        self.ai_cancelled = False
        self.elapsed = QElapsedTimer()
        self.tick = QTimer(self)
        self.tick.setInterval(1000)
        self.tick.timeout.connect(self._update_elapsed)

        self.original = FilePanel(self, "원본 설정", "캐릭터 원자료(원본 설정)가 여기에 표시됩니다.")
        self.result = FilePanel(self, "생성 결과", "검사할 생성 결과물이 여기에 표시됩니다.")

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 6)

        # ── 상단: 파일 불러오기 ──
        root.addLayout(self.original.top_row())
        root.addLayout(self.result.top_row())

        # ── 중앙: 원본 / 결과 보기 ──
        center = QSplitter(Qt.Horizontal)
        center.addWidget(self.original.box())
        center.addWidget(self.result.box())
        center.setSizes([1, 1])

        # ── 하단 왼쪽: 버튼 ──
        btn_panel = QFrame()
        btn_lay = QVBoxLayout(btn_panel)
        btn_lay.setContentsMargins(0, 0, 0, 0)
        self.mech_button = QPushButton("⚙ 기계 검사 실행")
        self.ai_button = QPushButton("🤖 AI 의미 검수 실행")
        self.clear_button = QPushButton("🧹 결과 지우기")
        self.save_button = QPushButton("💾 결과 저장")
        for b in (self.mech_button, self.ai_button):
            b.setMinimumHeight(48)
            b.setStyleSheet("font-weight:bold;")
        for b in (self.clear_button, self.save_button):
            b.setMinimumHeight(32)
        self.mech_button.setToolTip("글자 수, {{user}} 위치, 필수 섹션을 즉시 검사합니다.")
        self.ai_button.setToolTip("Claude Code 로 누락·규칙 약화·환각을 검사합니다. 몇 분 걸릴 수 있습니다.")
        self.save_button.setToolTip(f"검수 결과를 '{RESULT_DIR}' 폴더에 텍스트 파일로 저장합니다.")
        btn_lay.addWidget(self.mech_button)
        btn_lay.addWidget(self.ai_button)
        btn_lay.addSpacing(12)
        btn_lay.addWidget(self.clear_button)
        btn_lay.addWidget(self.save_button)
        btn_lay.addStretch(1)
        self.claude_label = QLabel()
        self.claude_label.setWordWrap(True)
        btn_lay.addWidget(self.claude_label)
        btn_panel.setFixedWidth(220)

        # ── 하단 오른쪽: 결과 체크리스트 ──
        result_box = QGroupBox("검수 결과")
        result_lay = QVBoxLayout(result_box)
        self.output = QTextBrowser()
        self.output.setOpenLinks(False)
        result_lay.addWidget(self.output)

        bottom = QWidget()
        bottom_lay = QHBoxLayout(bottom)
        bottom_lay.setContentsMargins(0, 0, 0, 0)
        bottom_lay.addWidget(btn_panel)
        bottom_lay.addWidget(result_box, 1)

        main_split = QSplitter(Qt.Vertical)
        main_split.addWidget(center)
        main_split.addWidget(bottom)
        main_split.setSizes([480, 360])
        root.addWidget(main_split, 1)
        # ── 탭: 작성 / 검수 ──
        self.review_tab = central
        self.writer = WriterTab(self)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.writer, "✏️ 작성")
        self.tabs.addTab(self.review_tab, "🔍 검수")
        corner = QWidget()
        cl = QHBoxLayout(corner)
        cl.setContentsMargins(0, 0, 8, 0)
        cl.addWidget(QLabel("규칙"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(available_modes())
        self.mode_combo.setCurrentText(rule_mode())
        self.mode_combo.setToolTip("1인: rules 폴더 / 다인: rules/다인 폴더의 템플릿·검수설정·작업지침을 씁니다.")
        self.mode_combo.currentTextChanged.connect(self._mode_changed)
        cl.addWidget(self.mode_combo)
        self.tabs.setCornerWidget(corner, Qt.TopRightCorner)
        self.setCentralWidget(self.tabs)

        self.mech_button.clicked.connect(self.run_mechanical)
        self.ai_button.clicked.connect(self.toggle_ai)
        self.clear_button.clicked.connect(self.clear_results)
        self.save_button.clicked.connect(self.save_results)

        self._refresh_claude_label()
        self.render()
        self.statusBar().showMessage("준비되었습니다. 원본 설정과 생성 결과 파일을 불러와 주세요.")
        if config_error:
            QMessageBox.warning(self, "설정 파일 오류", config_error)

    # ── 상태 표시 ──
    def _refresh_claude_label(self):
        if find_claude():
            self.claude_label.setText("<span style='color:#1a7f37'>● Claude Code 연결 가능</span>")
        else:
            self.claude_label.setText("<span style='color:#b7791f'>● Claude Code 미설치<br>"
                                      "(기계 검사만 사용 가능)</span>")

    def _update_elapsed(self):
        sec = self.elapsed.elapsed() // 1000
        self.statusBar().showMessage(f"🤖 {self.ai_label} 중... {sec}초 경과 (보통 1~3분 걸립니다. 창을 닫지 마세요)")

    # ── 기계 검사 ──
    def run_mechanical(self):
        if not self.result.text.strip():
            QMessageBox.information(self, "안내", "먼저 [생성 결과 불러오기] 버튼으로 검사할 파일을 불러와 주세요.")
            return
        self.config, err = load_config()  # 설정 파일을 고쳤을 수 있으니 매번 다시 읽음
        if err:
            QMessageBox.warning(self, "설정 파일 오류", err)
        self.mech_groups = run_mechanical_checks(self.original.text, self.result.text, self.config)
        self.render()
        self.statusBar().showMessage("기계 검사가 끝났습니다.", 5000)

    # ── AI 의미 검수 ──
    def _update_title(self):
        self.setWindowTitle(f"{APP_TITLE} · {rule_mode()} 모드")

    def _mode_changed(self, mode: str):
        if mode == rule_mode():
            return

        def revert():
            self.mode_combo.blockSignals(True)
            self.mode_combo.setCurrentText(rule_mode())
            self.mode_combo.blockSignals(False)

        if self.ai_busy():
            QMessageBox.information(self, "안내", "AI 작업이 끝난 뒤에 모드를 바꿔 주세요.")
            return revert()
        if self.writer.dirty and QMessageBox.question(
                self, "모드 바꾸기", "작성 탭에 저장하지 않은 내용이 있습니다. 모드를 바꾸면 사라집니다. 바꿀까요?") != QMessageBox.Yes:
            return revert()
        set_rule_mode(mode)
        try:
            MODE_FILE.write_text(mode, encoding="utf-8")
        except Exception:
            pass
        self.config, err = load_config()
        self.writer.dirty = False
        self.writer.new_doc(ask=False)
        self.mech_groups, self.ai_items, self.ai_note = [], [], ""
        self.render()
        self._update_title()
        self.statusBar().showMessage(f"{mode} 모드로 바꿨습니다. ({rules_dir()} 폴더의 규칙을 씁니다)", 6000)
        if err:
            QMessageBox.warning(self, "설정 파일 오류", err)

    # ── AI 작업 (검수·다듬기 공용, 한 번에 하나만) ──
    def ai_busy(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def start_ai(self, prompt: str, instruction: str, on_done, label: str):
        self.config, _ = load_config()
        self.ai_label, self.ai_callback = label, on_done
        self.worker = AiWorker(prompt, int(self.config.get("AI_제한시간_초", 600)), instruction)
        self.worker.done.connect(self._ai_job_done)
        self.ai_button.setText("⏹ AI 작업 중지")
        self.mech_button.setEnabled(False)
        self.writer.set_ai_running(True)
        self.elapsed.start()
        self.tick.start()
        self._update_elapsed()
        self.worker.start()

    def _ai_job_done(self, ok: bool, text: str):
        self.tick.stop()
        self.ai_button.setText("🤖 AI 의미 검수 실행")
        self.mech_button.setEnabled(True)
        self.writer.set_ai_running(False)
        self.ai_cancelled = bool(self.worker and self.worker.cancelled)
        sec = self.elapsed.elapsed() // 1000
        self.statusBar().showMessage(f"{self.ai_label} {'완료' if ok else '중지/실패'} ({sec}초)", 8000)
        callback, self.ai_callback = self.ai_callback, None
        if callback:
            callback(ok, text)

    def toggle_ai(self):
        if self.ai_busy():
            self.worker.cancel()
            self.statusBar().showMessage("AI 작업을 중지하는 중입니다...")
            return
        if not self.original.text.strip() or not self.result.text.strip():
            QMessageBox.information(self, "안내", "AI 의미 검수는 원본 설정과 생성 결과 파일이 모두 필요합니다.")
            return
        if not find_claude():
            self._refresh_claude_label()
            QMessageBox.warning(self, "Claude Code 없음", CLAUDE_MISSING_MSG)
            return

        self.ai_items, self.ai_note = [], "AI 의미 검수가 진행 중입니다..."
        self.render()
        self.start_ai(build_ai_prompt(self.original.text, self.result.text), CLI_INSTRUCTION,
                      self._ai_finished, "AI 의미 검수")

    def _ai_finished(self, ok: bool, text: str):
        sec = self.elapsed.elapsed() // 1000
        if not ok:
            self.ai_items, self.ai_note = [CheckItem(FAIL, "AI 의미 검수를 완료하지 못했습니다", text)], ""
            self.statusBar().showMessage("AI 의미 검수 실패", 8000)
        else:
            items = parse_ai_output(text)
            if items is None:
                self.ai_items = [CheckItem(WARN, "AI 응답을 체크리스트로 바꾸지 못했습니다. 원문을 그대로 보여드립니다.",
                                           text.strip())]
            else:
                self.ai_items = items
            self.ai_note = f"소요 시간 {sec}초"
            self.statusBar().showMessage(f"AI 의미 검수가 끝났습니다. ({sec}초)", 8000)
        self.render()

    # ── 결과 표시 ──
    def _all_items(self) -> list[CheckItem]:
        return [it for _, items in self.mech_groups for it in items] + self.ai_items

    def render(self):
        all_items = self._all_items()
        if not all_items and not self.ai_note:
            self.output.setHtml(
                "<div style='color:#5f6b7a; padding:8px;'>"
                "<p><b>사용 순서</b></p>"
                "<p>① 위쪽 버튼으로 <b>원본 설정</b>과 <b>생성 결과</b> 파일을 불러옵니다.</p>"
                "<p>② <b>⚙ 기계 검사 실행</b> 버튼을 눌러 글자 수·{{user}}·필수 섹션을 확인합니다.</p>"
                "<p>③ <b>🤖 AI 의미 검수 실행</b> 버튼을 눌러 누락·규칙 약화·환각을 확인합니다.</p>"
                "<p>결과 표시: <span style='color:#1a7f37'>✔ 정상</span> · "
                "<span style='color:#b7791f'>▲ 경고</span> · <span style='color:#c62828'>❌ 실패</span></p>"
                "</div>")
            return

        parts = [self._summary_html(all_items)]
        if self.mech_groups:
            parts.append("<h3 style='margin:10px 0 2px 0;'>1단계 · 기계 검사</h3>")
            for title, items in self.mech_groups:
                parts.append(f"<p style='margin:8px 0 2px 0; color:#334155;'><b>■ {html.escape(title)}</b></p>")
                parts.extend(self._item_html(it) for it in items)
        if self.ai_items or self.ai_note:
            note = f" <span style='color:#5f6b7a; font-size:small;'>({html.escape(self.ai_note)})</span>" if self.ai_note else ""
            parts.append(f"<h3 style='margin:14px 0 2px 0;'>2단계 · AI 의미 검수{note}</h3>")
            parts.extend(self._item_html(it) for it in self.ai_items)
        self.output.setHtml("".join(parts))

    @staticmethod
    def _summary_html(items: list[CheckItem]) -> str:
        cnt = {s: sum(1 for it in items if it.status == s) for s in (PASS, WARN, FAIL)}
        if cnt[FAIL]:
            verdict, color = "수정이 필요합니다", STATUS_STYLE[FAIL][1]
        elif cnt[WARN]:
            verdict, color = "확인이 필요한 항목이 있습니다", STATUS_STYLE[WARN][1]
        else:
            verdict, color = "모든 항목 통과", STATUS_STYLE[PASS][1]
        return (f"<p style='font-size:large;'><b style='color:{color};'>{verdict}</b> &nbsp; "
                f"<span style='color:{STATUS_STYLE[PASS][1]}'>✔ {cnt[PASS]}</span> &nbsp; "
                f"<span style='color:{STATUS_STYLE[WARN][1]}'>▲ {cnt[WARN]}</span> &nbsp; "
                f"<span style='color:{STATUS_STYLE[FAIL][1]}'>❌ {cnt[FAIL]}</span></p>")

    @staticmethod
    def _item_html(it: CheckItem) -> str:
        icon, color = STATUS_STYLE[it.status]
        detail = ""
        if it.detail:
            detail = ("<div style='margin-left:26px; color:#475569;'>"
                      + html.escape(it.detail).replace("\n", "<br>") + "</div>")
        return (f"<div style='margin:3px 0;'><span style='color:{color}; font-weight:bold;'>{icon}</span>&nbsp; "
                f"<span style='color:{color if it.status != PASS else '#111827'};'>{html.escape(it.title)}</span>"
                f"{detail}</div>")

    def clear_results(self):
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "안내", "AI 검수가 진행 중입니다. 끝난 뒤에 지워 주세요.")
            return
        self.mech_groups, self.ai_items, self.ai_note = [], [], ""
        self.render()

    def save_results(self):
        if not self._all_items():
            QMessageBox.information(self, "안내", "저장할 검수 결과가 없습니다. 먼저 검사를 실행해 주세요.")
            return
        icon = {PASS: "[정상]", WARN: "[경고]", FAIL: "[실패]", INFO: "[참고]"}
        lines = [APP_TITLE, f"검수 시각: {datetime.now():%Y-%m-%d %H:%M:%S}",
                 f"원본 설정: {self.original.path_edit.text() or '-'}",
                 f"생성 결과: {self.result.path_edit.text() or '-'}", ""]
        sections = [(f"[1단계 기계 검사] {t}", items) for t, items in self.mech_groups]
        if self.ai_items:
            sections.append(("[2단계 AI 의미 검수]", self.ai_items))
        for title, items in sections:
            lines.append(f"■ {title}")
            for it in items:
                lines.append(f"  {icon[it.status]} {it.title}")
                lines.extend(f"        {d}" for d in it.detail.splitlines())
            lines.append("")
        try:
            RESULT_DIR.mkdir(exist_ok=True)
            path = RESULT_DIR / f"검수결과_{datetime.now():%Y%m%d_%H%M%S}.txt"
            path.write_text("\n".join(lines), encoding="utf-8-sig")
        except Exception as e:
            QMessageBox.warning(self, "저장 실패", f"결과를 저장하지 못했습니다.\n{e}")
            return
        QMessageBox.information(self, "저장 완료", f"검수 결과를 저장했습니다.\n{path}")

    def closeEvent(self, event):
        if self.writer.dirty and QMessageBox.question(
                self, "종료 확인", "작성 탭에 저장하지 않은 내용이 있습니다. 저장하지 않고 종료할까요?") != QMessageBox.Yes:
            event.ignore()
            return
        if self.ai_busy():
            answer = QMessageBox.question(self, "종료 확인", "AI 작업이 아직 진행 중입니다. 중지하고 종료할까요?")
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            self.worker.cancel()
            self.worker.wait(5000)
        event.accept()


def install_error_dialog():
    """예상치 못한 오류가 나도 프로그램이 조용히 꺼지지 않고 안내 창을 띄웁니다."""

    def hook(exc_type, exc, tb):
        detail = "".join(traceback.format_exception(exc_type, exc, tb))
        sys.__stderr__ and sys.__stderr__.write(detail)
        box = QMessageBox(QMessageBox.Critical, "오류가 발생했습니다",
                          "예상치 못한 오류가 발생했습니다.\n'Show Details...' 내용을 복사해서 개발자에게 전달해 주세요.")
        box.setDetailedText(detail)
        box.exec()

    sys.excepthook = hook


def main():
    if getattr(sys, "frozen", False):
        # exe 로 실행하면 바로가기 등에서 시작 위치가 달라질 수 있으므로,
        # exe 가 있는 폴더를 '현재 실행 위치'로 맞춰 rules 폴더를 찾게 합니다.
        os.chdir(Path(sys.executable).parent)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    if sys.platform == "win32":
        app.setFont(QFont("맑은 고딕", 10))
    install_error_dialog()
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
