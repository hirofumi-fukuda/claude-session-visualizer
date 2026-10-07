"""台帳本文の軽量 markdown → HTML（外部ライブラリなし・台帳で実際に使う記法だけ）。

counter_gen.py の _md/_inline を切り出して共有可能にしたもの。
track_record の個別ページ（台帳全文レンダリング）と日誌ビュー（daily_view）で共有する。
対応記法: 段落 / 箇条書き（ネスト）/ 表 / コードブロック / 見出し / 罫線 / 引用 /
          インライン（code・strong・[[wiki]]・[text](url)・裸URL自動リンク）。
相対リンク [text](path) は render/_inline に link（path -> href）を渡した時だけリンク化する。
"""
import html as html_mod
import re


def _inline(s: str, link=None) -> str:
    s = html_mod.escape(s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"\[\[([^\]]+)\]\]", r'<span class="wiki">[[\1]]</span>', s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2">\1</a>', s)
    if link:  # 相対/絶対パスのリンク（http(s) は上で処理済み）
        def _rel(m):
            href = link(html_mod.unescape(m.group(2)))
            return f'<a href="{html_mod.escape(href)}">{m.group(1)}</a>' if href else m.group(0)
        s = re.sub(r"\[([^\]]+)\]\((?!https?://)([^)\s]+)\)", _rel, s)
    # 裸URLの自動リンク。エスケープ済みなので &lt; &gt; &quot; の手前で止める（&amp; はURLの一部）
    s = re.sub(r"(?<![\"'>=])(https?://(?:(?!&(?:lt|gt|quot);)[^\s<）)])+)",
               r'<a href="\1">\1</a>', s)
    return s


def render(text: str, link=None) -> str:
    """台帳本文を HTML に。段落 / 箇条書き（ネスト）/ 表 / コードブロック / 見出し / 罫線 / 引用。"""
    out, lines, i = [], (text or "").splitlines(), 0
    stack, para = [], []      # stack: 開いている <ul> のインデント深さ
    _in = lambda x: _inline(x, link)  # noqa: E731

    def close_lists(to: int = 0):
        while len(stack) > to:
            stack.pop()
            out.append("</ul>")

    def flush_para():
        if para:
            out.append("<p>" + "<br>".join(_in(x) for x in para) + "</p>")
            para.clear()

    while i < len(lines):
        ln = lines[i]
        s = ln.strip()
        if s.startswith("```"):                                   # コードブロック
            flush_para(); close_lists()
            buf, i = [], i + 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i]); i += 1
            out.append("<pre>" + html_mod.escape("\n".join(buf)) + "</pre>")
            i += 1
            continue
        if not s:
            flush_para(); close_lists()
            i += 1
            continue
        if s.startswith(">"):                                     # 引用（中身は再帰で描画）
            flush_para(); close_lists()
            buf = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(re.sub(r"^\s*> ?", "", lines[i])); i += 1
            out.append("<blockquote>" + render("\n".join(buf), link) + "</blockquote>")
            continue
        if s.startswith("|") and s.endswith("|"):                  # 表
            flush_para(); close_lists()
            rows, i = [], i
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            rows = [r for r in rows if not all(set(c) <= set("-: ") for c in r)]
            if rows:
                out.append("<table><tr>" + "".join(f"<th>{_in(c)}</th>" for c in rows[0])
                           + "</tr>")
                for r in rows[1:]:
                    out.append("<tr>" + "".join(f"<td>{_in(c)}</td>" for c in r) + "</tr>")
                out.append("</table>")
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            flush_para(); close_lists()
            out.append(f"<h{min(len(m.group(1)) + 2, 4)}>{_in(m.group(2))}"
                       f"</h{min(len(m.group(1)) + 2, 4)}>")
            i += 1
            continue
        if re.match(r"^(-{3,}|\*{3,}|_{3,})$", s):
            flush_para(); close_lists()
            out.append("<hr>")
            i += 1
            continue
        m = re.match(r"^([-*・]|\d+\.)\s+(.*)$", s)
        if m:
            flush_para()
            depth = (len(ln) - len(ln.lstrip())) // 2 + 1
            while len(stack) > depth:
                close_lists(len(stack) - 1)
            while len(stack) < depth:
                stack.append(depth); out.append("<ul>")
            out.append(f"<li>{_in(m.group(2))}</li>")
            i += 1
            continue
        if stack and out and out[-1].endswith("</li>"):   # 箇条書きの折り返し行
            out[-1] = out[-1][:-len("</li>")] + "<br>" + _in(s) + "</li>"
        elif stack:
            out.append(f"<li>{_in(s)}</li>")
        else:
            para.append(s)
        i += 1
    flush_para(); close_lists()
    return "\n".join(out)
