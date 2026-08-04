"""台帳（auto-memory の memory/*.md）を読む共通部＝エージェントカウンターの土台。

正本は台帳ファイルそのもの。ここは毎回ファイルから読むだけで内容を保持しない（二重管理の禁止）。
`~/.claude` へは読み取りのみ。標準セクションに揃っていない台帳は先頭N行にフォールバックする。

台帳の判定は frontmatter の `type: project`（両ワークスペースで統一されている）。
見出しレベルは台帳ごとに `##` / `###` が混在するので、レベルではなく見出し語で拾う。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# frontmatter（--- で挟まれた簡易YAML。metadata: の入れ子もフラットに拾えれば足りる）
FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
HEAD_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$", re.M)
# 「（なし）」「- （空）」「（なし。S2で4通消化: …）」等＝消化済みマーカー。
# 消化のログを（なし）の後ろに書き足す運用があるので、先頭行が「（なし」で始まれば空欄とみなす。
EMPTY_RE = re.compile(r"^[\s\-*・]*[（(](?:なし|無し|空|none|no)", re.I)

# ── セッション履歴テーブル ──────────────────────────────────────────────
KW_SESSION = ("セッション履歴", "session history", "session track", "session tracks", "セッション採番")
# track_now() 専用: KW_CURRENT より広いフォールバック（"now /" で "Now / Next / Blocked" を拾う）
KW_NOW_WIDE = ("現在地", "最新ステータス", "ステータス", "アクティブ", "now /", "現状")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

# 見出し語（表記ゆれを含めてここに集約する。ワークスペース差もここで吸収）
KW_CURRENT = ("現在地", "最新ステータス", "ステータス")
KW_NEXT = ("next", "次の一手", "次アクション", "残タスク")
KW_BLOCK = ("ブロッカー", "判断待ち")
# 依頼票＝📨付きの見出しだけ。「未解決・次回への申し送り」のような自分向けメモを拾わないため、
# 標準セクション規約（## 📨 申し送り）どおり絵文字を必須にする（揃える圧力は副次効果として歓迎）。
KW_INBOX = ("📨",)


@dataclass
class Section:
    level: int
    title: str
    body: str

    @property
    def is_empty(self) -> bool:
        lines = [ln for ln in self.body.splitlines() if ln.strip()]
        return not lines or bool(EMPTY_RE.match(lines[0]))

    @property
    def n_lines(self) -> int:
        return len([ln for ln in self.body.splitlines() if ln.strip()])


@dataclass
class Ledger:
    path: Path
    ws_key: str
    ws_label: str
    name: str = ""
    description: str = ""
    modified: str = ""
    lead: str = ""                      # 最初の見出しより前の本文（見出しゼロの台帳の受け皿）
    sections: list = field(default_factory=list)
    project: str | None = None          # 案件名（config の alias で引けたときだけ入る）

    @property
    def label(self) -> str:
        """表示名。案件名が引ければそれ、無ければファイル名。"""
        return self.project or self.path.stem

    def pick(self, keywords) -> Section | None:
        return next(iter(self.pick_all(keywords)), None)

    def pick_all(self, keywords) -> list:
        kws = [k.casefold() for k in keywords]
        return [s for s in self.sections
                if any(k in s.title.casefold() for k in kws)]

    def current(self, max_lines: int = 4) -> str:
        """現在地の先頭数行。標準セクションが無ければリード文→本文先頭にフォールバック。"""
        sec = self.pick(KW_CURRENT)
        text = sec.body if sec and not sec.is_empty else self.lead
        if not text.strip() and self.sections:
            text = self.sections[0].body
        return head_lines(text, max_lines)

    def inbox(self) -> list:
        """未消化の📨申し送り（空欄マーカーだけの欄は落とす）。"""
        return [s for s in self.pick_all(KW_INBOX) if not s.is_empty]

    def next_up(self, max_lines: int = 3) -> str:
        sec = self.pick(KW_NEXT)
        return head_lines(sec.body, max_lines) if sec and not sec.is_empty else ""

    def blockers(self, max_lines: int = 3) -> str:
        sec = self.pick(KW_BLOCK)
        return head_lines(sec.body, max_lines) if sec and not sec.is_empty else ""

    def session_history(self) -> list:
        """セッション履歴テーブルの全エントリ（予約行含む）。

        同じ台帳に複数の KW_SESSION マッチセクションがある場合、
        テーブル行が実在する最初のセクションを使う。
        （例: "GWR運用（採番台帳）" は metadata 文章のみで空 → "🔢 セッション履歴" に落ちる）
        """
        for sec in self.pick_all(KW_SESSION):
            entries = parse_session_table(sec.body)
            if entries:
                return entries
        return []

    def last_sessions(self, n: int = 3) -> list:
        """直近N件のセッション（予約行を除く）。新しい順。"""
        hist = [e for e in self.session_history() if not e.is_reserved]
        return list(reversed(hist[-n:]))

    def session_count(self) -> int:
        return len([e for e in self.session_history() if not e.is_reserved])

    def active_since(self) -> str:
        """最初のセッション日付（YYYY-MM-DD）。"""
        hist = [e for e in self.session_history() if e.date]
        return hist[0].date if hist else ""

    def latest_session_date(self) -> str:
        """最後のセッション日付（YYYY-MM-DD）。"""
        hist = [e for e in self.session_history() if e.date]
        return hist[-1].date if hist else ""

    def track_now(self, max_lines: int = 2) -> str:
        """track_record 向けの現在地テキスト（KW_NOW_WIDE でより広くフォールバック）。"""
        for kws in [KW_CURRENT, KW_NOW_WIDE]:
            sec = self.pick(kws)
            if sec and not sec.is_empty:
                return head_lines(sec.body, max_lines)
        return head_lines(self.lead, max_lines) if self.lead else ""


@dataclass
class SessionEntry:
    session: str   # "S0", "S1" 等（元の表記を保持）
    date: str      # "2026-07-29"（DATE_RE で抽出済み。予約/空は ""）
    summary: str   # 内容 / テーマ
    result: str    # 結果（"✅ ..." / "—" / ""）

    @property
    def is_done(self) -> bool:
        return any(c in self.result for c in ("✅", "✓", "完了", "Done", "done"))

    @property
    def is_reserved(self) -> bool:
        return not self.date

    @property
    def n(self) -> int | None:
        m = re.search(r"\d+", self.session)
        return int(m.group()) if m else None


def slug(name: str, prefix: str = "") -> str:
    """台帳ページのファイル名。ライブボード側から案件名だけで同じURLを組めるよう共有する。"""
    s = re.sub(r"[^\w\-ぁ-んァ-ヴ一-龥ー]+", "-", name, flags=re.U).strip("-")
    return f"{prefix}{s[:60] or 'x'}"


def head_lines(text: str, n: int) -> str:
    lines = [ln.rstrip() for ln in (text or "").splitlines() if ln.strip()]
    return "\n".join(lines[:n])


def parse_session_table(text: str) -> list:
    """markdown テーブルをパースして SessionEntry のリストを返す。

    ヘッダー行の列名は `#`/`S`/`セッション` 等が混在するため無視し、
    「先頭セルが S + 数字 形式」で本物のデータ行を識別する。
    """
    entries = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not (line.startswith("|") and line.endswith("|")):
            continue
        cells = [c.strip() for c in line[1:-1].split("|")]
        while len(cells) < 4:
            cells.append("")
        s, raw_date, summary, result = cells[0], cells[1], cells[2], cells[3]
        # セパレータ行（--- 等）をスキップ
        if all(set(c) <= {"-", ":", " "} for c in cells[:4] if c):
            continue
        # ヘッダー行（数字を含まない識別子行）をスキップ
        if not re.search(r"\d", s) and len(s) <= 6:
            continue
        # S\d+ 形式でない行はスキップ
        if not re.match(r"^S\s*\d", s, re.I):
            continue
        dm = DATE_RE.search(raw_date)
        date = dm.group() if dm else ""
        entries.append(SessionEntry(session=s, date=date,
                                    summary=summary, result=result))
    return entries


def parse_frontmatter(text: str) -> tuple[dict, str]:
    m = FM_RE.match(text)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        key, sep, val = line.strip().partition(":")
        if sep and val.strip():
            meta.setdefault(key.strip(), val.strip())
    return meta, text[m.end():]


def split_sections(body: str) -> tuple[str, list]:
    """本文を (リード文, [Section]) に割る。"""
    heads = list(HEAD_RE.finditer(body))
    if not heads:
        return body.strip(), []
    lead = body[:heads[0].start()].strip()
    out = []
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(body)
        out.append(Section(len(h.group(1)), h.group(2).strip(),
                           body[h.end():end].strip("\n")))
    return lead, out


def resolve_project(mapper, *candidates) -> str | None:
    """config の project_aliases で案件名を引く。引けなければ None（＝案件未対応の台帳）。

    mapper._resolve_project は未ヒット時に入力をそのまま返すので判定に使えない。
    部分一致は4文字以上に絞る（短い別名がファイル名に紛れ込む誤爆を避ける）。
    """
    for raw in candidates:
        if not raw:
            continue
        key = mapper._norm(raw)
        if key in mapper.alias_to_canonical:
            return mapper.alias_to_canonical[key]
        hits = [a for a in mapper.aliases_by_len if len(a) >= 4 and a in key]
        if hits:
            return mapper.alias_to_canonical[hits[0]]
    return None


def load_ledger(path: Path, ws_key: str, ws_label: str, mapper=None) -> Ledger | None:
    """1ファイルを台帳として読む。type が project でなければ None。"""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    meta, body = parse_frontmatter(text)
    if meta.get("type") != "project":
        return None
    lead, sections = split_sections(body)
    led = Ledger(path=path, ws_key=ws_key, ws_label=ws_label,
                 name=meta.get("name", ""), description=meta.get("description", ""),
                 modified=meta.get("modified", ""), lead=lead, sections=sections)
    if mapper is not None:
        led.project = resolve_project(mapper, led.name, path.stem)
    return led


def memory_dir(claude_dir: Path, ws_key: str) -> Path:
    return claude_dir / "projects" / ws_key / "memory"


def load_ledgers(claude_dir: Path, ws_labels: dict, mapper=None) -> list:
    """全ワークスペースの台帳を読む（MEMORY.md＝索引と回覧板は除く）。"""
    out = []
    for ws_key, ws_label in ws_labels.items():
        mem = memory_dir(claude_dir, ws_key)
        if not mem.is_dir():
            continue
        for p in sorted(mem.glob("*.md")):
            if p.name in ("MEMORY.md", BULLETIN_NAME):
                continue
            led = load_ledger(p, ws_key, ws_label, mapper)
            if led:
                out.append(led)
    return out


_GENERIC_STEMS = frozenset({
    "00-現在地", "00現在地", "現在地", "readme", "index", "session-tracks",
})


def load_track_file(path: Path, ws_key: str, ws_label: str, mapper=None) -> "Ledger | None":
    """frontmatter の type:project がなくても セッション履歴 セクションがあれば読む。

    project_dirs / extra_track_files（gitリポジトリの現在地ファイル等）向け。
    mapper は使わず抽出した名前をそのまま使う（长い見出しや部分一致の誤爆を防ぐため）。
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    meta, body = parse_frontmatter(text)
    lead, sections = split_sections(body)

    has_project_type = meta.get("type") == "project"
    has_session = any(
        any(k.casefold() in s.title.casefold() for k in KW_SESSION)
        for s in sections
    )
    if not (has_project_type or has_session):
        return None

    # 案件名の決定（優先順位）:
    #   1. frontmatter の name フィールド
    #   2. 意味のある stem（"00-現在地" 等の汎用名でない場合）
    #   3. 最初の見出し（em-dash 以降のサフィックスを除去）
    name = meta.get("name", "")
    if not name:
        stem_clean = re.sub(r"^\d+[_\-]", "", path.stem)
        if stem_clean.lower() not in _GENERIC_STEMS:
            name = stem_clean   # "GEMiNX本体開発", "ホットフォルダー脱却" 等
        elif sections:
            # ジェネリックな stem（"00-現在地"等）の場合は見出しから取る
            first_title = sections[0].title
            name = re.split(r"\s*[—\-]{1,2}\s*(?:現在地|台帳|正本|セッション)", first_title)[0].strip()
        if not name:
            name = stem_clean or path.stem

    led = Ledger(path=path, ws_key=ws_key, ws_label=ws_label,
                 name=name, description=meta.get("description", ""),
                 modified=meta.get("modified", ""), lead=lead, sections=sections)
    # mapper は使わず抽出名をそのまま project に設定
    # （project_aliases の部分一致誤爆を防ぐ。memory台帳の resolve_project とは別管理）
    led.project = name or path.stem
    return led


def load_project_dirs(dirs: list, ws_key: str, ws_label: str, mapper=None) -> list:
    """指定ディレクトリの *.md を load_track_file でスキャン。"""
    out = []
    for d in dirs:
        p = Path(d).expanduser()
        if not p.is_dir():
            continue
        for f in sorted(p.glob("*.md")):
            led = load_track_file(f, ws_key, ws_label, mapper)
            if led:
                out.append(led)
    return out


def load_extra_files(paths: list, ws_label: str, mapper=None) -> list:
    """個別指定ファイル（gitリポジトリ等）を load_track_file で読む。"""
    out = []
    for raw in paths:
        f = Path(raw).expanduser()
        if not f.is_file():
            continue
        led = load_track_file(f, f.parent.name, ws_label, mapper)
        if led:
            out.append(led)
    return out


def find_ledgers(ledgers: list, query: str, mapper=None) -> list:
    """案件名（別名可）で台帳を引く。alias 解決 → 表示名/ファイル名/name の部分一致の順。"""
    q = re.sub(r"\s+", "", query).casefold()
    if mapper is not None:
        canon = resolve_project(mapper, query)
        if canon:
            hit = [l for l in ledgers if l.project == canon]
            if hit:
                return hit
    exact = [l for l in ledgers if re.sub(r"\s+", "", l.label).casefold() == q]
    if exact:
        return exact
    return [l for l in ledgers
            if q in re.sub(r"\s+", "", f"{l.label}{l.path.stem}{l.name}").casefold()]


# ── 回覧板（宛先なし・読めば終わりの全体周知。書くのは司令塔）──────────────
BULLETIN_NAME = "BULLETIN.md"


def load_bulletin(claude_dir: Path, ws_labels: dict) -> list:
    """各ワークスペースの回覧板を (ws_label, path, 本文) で返す。掲示ゼロなら出さない。

    ファイルには運用ルールの説明文が常駐するので、掲示する中身は「## 掲示」欄だけを見る
    （欄が「（なし）」なら何も出さない＝司令塔が下げた状態）。
    """
    out = []
    for ws_key, ws_label in ws_labels.items():
        p = memory_dir(claude_dir, ws_key) / BULLETIN_NAME
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        lead, sections = split_sections(parse_frontmatter(text)[1])
        posts = [s for s in sections if "掲示" in s.title]
        body = posts[0].body.strip() if posts else lead.strip()
        if body and not (posts and posts[0].is_empty):
            out.append((ws_label, p, body))
    return out
