# sprites — オフィス配置図（reports/office.html）のキャラ絵

ここに置いた絵は office.html にインライン埋め込みされる（ファイル参照切れは起きない）。
無ければコード内のピクセル絵（claude_sv/office.py の SPR/HS）が使われる。

## 受け入れ仕様
- 透過 PNG（SVG も可）。表示は 36×48px 相当に縮小。ドット絵なら 12×16〜24×32 がにじまない
- ファイル名（固定）:
  - `human.png` … 人間（最後に話しかけた席の横に立つ1人）
  - `agent.png` … エージェント共通（グレー1枚＝モデル色を色相で乗せる）
  - または `agent-opus.png` / `agent-sonnet.png` / `agent-haiku.png` / `agent-fable.png`（モデル別に描く場合・共通より優先）

## テーマ（サブフォルダ）
- `assets/sprites/<テーマ>/` に上と同じファイル名で置くと、office.html ヘッダ右の 🎨 プルダウンで切り替えられる（選択は localStorage・`?spr=<テーマ>` で URL 指定も可）
- 人間の候補が複数あるなら `human-2.png` `human-3.png` … → 「<テーマ>（人間2）」として別の選択肢になる
- 直下の固定名は「既定」。`samples/` は作業用（絵の正本 `themes/*.py` と生成 `gen.py`）で読み込み対象外
