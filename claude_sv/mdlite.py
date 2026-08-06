"""台帳本文の軽量 markdown → HTML（外部ライブラリなし・台帳で実際に使う記法だけ）。

counter_gen.py の _md/_inline を切り出して共有可能にしたもの。
track_record の個別ページ（台帳全文レンダリング）と将来のカウンター復活で共有する。
対応記法: 段落 / 箇条書き（ネスト）/ 表 / コードブロック / 見出し / 罫線 /
          インライン（code・strong・[[wiki]]・[text](url)・裸URL自動リンク）。
"""
import html as html_mod
import re


def _inline(s: str) -> str:
    s = html_mod.escape(s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"\[\[([^\]]+)\]\]", r'<span class="wiki">[[\1]]</span>', s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2">\1</a>', s)
    # 裸URLの自動リンク。エスケープ済みなので &lt; &gt; &quot; の手前で止める（&amp; はURLの一部）
    s = re.sub(r"(?<![\"'>=])(https?://(?:(?!&(?:lt|gt|quot);)[^\s<）)])+)",
               r'<a href="\1">\1</a>', s)
    return s


def render(text: str) -> str:
    """台帳本文を HTML に。段落 / 箇条書き（ネスト）/ 表 / コードブロック / 見出し / 罫線。"""
    out, lines, i = [], (text or "").splitlines(), 0
    stack, para = [], []      # stack: 開いている <ul> のインデント深さ

    def close_lists(to: int = 0):
        while len(stack) > to:
            stack.pop()
            out.append("</ul>")

    def flush_para():
        if para:
            out.append("<p>" + "<br>".join(_inline(x) for x in para) + "</p>")
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
        if s.startswith("|") and s.endswith("|"):                  # 表
            flush_para(); close_lists()
            rows, i = [], i
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            rows = [r for r in rows if not all(set(c) <= set("-: ") for c in r)]
            if rows:
                out.append("<table><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in rows[0])
                           + "</tr>")
                for r in rows[1:]:
                    out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>")
                out.append("</table>")
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            flush_para(); close_lists()
            out.append(f"<h{min(len(m.group(1)) + 2, 4)}>{_inline(m.group(2))}"
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
            out.append(f"<li>{_inline(m.group(2))}</li>")
            i += 1
            continue
        if stack and out and out[-1].endswith("</li>"):   # 箇条書きの折り返し行
            out[-1] = out[-1][:-len("</li>")] + "<br>" + _inline(s) + "</li>"
        elif stack:
            out.append(f"<li>{_inline(s)}</li>")
        else:
            para.append(s)
        i += 1
    flush_para(); close_lists()
    return "\n".join(out)
