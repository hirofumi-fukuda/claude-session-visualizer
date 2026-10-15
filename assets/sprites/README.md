# sprites — オフィス配置図（reports/office.html）のキャラ絵

ここに置いた絵は office.html にインライン埋め込みされる（ファイル参照切れは起きない）。
無ければコード内のピクセル絵（claude_sv/office.py の SPR/HS）が使われる。

## 受け入れ仕様
- 透過 PNG（SVG も可）。表示は 36×48px 相当に縮小。ドット絵なら 12×16〜24×32 がにじまない
- ファイル名（固定）:
  - `human.png` … 人間（最後に話しかけた席の横に立つ1人）
  - `agent.png` … エージェント共通（グレー1枚＝モデル色を色相で乗せる）
  - または `agent-opus.png` / `agent-sonnet.png` / `agent-haiku.png` / `agent-fable.png`（モデル別に描く場合・共通より優先）
