# claude-wt — Windows Terminal で claude CLI をタブ束ね（たたき台）

Cursor拡張の代わりに、Windows Terminal 1窓のタブに複数セッションを束ねる最軽量ランチャー。
**拡張と同じ履歴基盤**（`~/.claude/projects/*.jsonl`）に書くので、採番合言葉・ライブボード・
トースト/TTSフックは全部そのまま生きる。拡張との併走OK（作られた: 2026-08-09・名前は仮）。

## 使い方

```powershell
cd C:\source\git-h.fukuda1207\claude-session-visualizer\claude-wt
.\launch.ps1                # 全ワークスペースのタブを開く（各タブで合言葉を打つ）
.\launch.ps1 -Continue      # 各ディレクトリの最新セッションを再開（claude -c）＝復帰用
.\launch.ps1 -Only ハブ     # 名前の部分一致で絞る（カンマ区切り可: -Only "ハブ,GEMiNX"）
.\launch.ps1 -DryRun        # 実行せず wt コマンドを表示（デバッグ）
```

- 再実行すると同じ「claude」ウインドウにタブが**追加**される（同名タブの重複チェックはしない）
- タブを増やす: `workspaces.json` の `workspaces` に1行足すだけ（name/path/color）
- claude 本体は自動探索（PATH → `~\.local\bin` → Cursor/VSCode拡張同梱の最新）。
  拡張のアップデートでパスが変わっても追従する。固定したければ `claude_cmd` にフルパス

## 前回のタブ状態を復元（restore_tabs.ps1・2026-08-10 会社S15新設）

タブ構成は毎回同じではないので、固定タブ（workspaces.json）とは別に「前回状態」を復元できる:

- ライブボードの watcher が稼働セッション一覧を `..\reports\tabs_snapshot.json` に常時保存
  （空では上書きしない＝全タブを閉じても・PC再起動しても「最後に動いていた状態」が残る）
- `restore_tabs.ps1` がそれを読んで `claude --resume` のタブを束ね起動
  - 稼働中のセッションは自動スキップ（`claude agents --json` 照合＝同一セッションの二重resume防止）
  - `-Only 案件名` で絞り込み（カンマ区切り可）・`-DryRun` で何が開くか確認のみ

```powershell
.\restore_tabs.ps1          # 前回の稼働セッションをまとめて復元
.\restore_tabs.ps1 -DryRun  # 何が開くか確認だけ
```

前提: ライブボード（`live_board.bat`）を普段から動かしていること（＝スナップショットの書き手）。

## ボードからの起動（claude-wt:// プロトコル・2026-08-10 会社S15）

`register_protocol.ps1` を一度実行すると、ライブボードのボタンからWTタブを直接起動できる:

- **▶ 開く**（休眠セクション）= `claude-wt://resume/<uuid>` … 休眠セッションをワンクリック復帰
- **➕ 新規WTタブ**（ボード上部のWS別ボタン列）= `claude-wt://new/<ワークスペース名>` …
  指定ワークスペースで素の claude を新規起動（タブで採番合言葉を打つ）
  - 新規は「**どのワークスペースで焚くか**」が必須入力（cwd がセッションの所属先＝
    採番・メモリ・地図の入り口を決める）。ボタンの選択肢とハンドラ側の検証ホワイトリストは
    どちらも `workspaces.json`＝タブを増やしたければそこに1行足すだけ
- セキュリティ: URLに載るのは **UUID / ワークスペース名のみ**（パス・コマンドは常にローカルの
  transcript / workspaces.json から解決＝ブラウザからの注入面なし）
- 補足: 新規タブは**最初の発話をするまでボードに載らない**（会話ゼロ＝jsonl未生成のセッションは
  空セッションフィルタで非表示・仕様）

## 画像の貼り付け（ここだけ流儀が変わる）

ネイティブWindowsのCLIは**クリップボード画像の直貼り（Alt+V）が黙って失敗する既知バグ**あり。
代わりに「ファイル経由」で渡す:

1. `Win+Shift+S` でスクショ（クリップボードへ）
2. `clip2file.cmd` をダブルクリック（→ `clips\` にPNG保存＋**パスがクリップボードに入る**）
3. claude のプロンプトで `Ctrl+V`（パスが貼られる）→ そのまま送信すれば読んでくれる

- 画像**ファイル**なら端末へドラッグ&ドロップでもOK（パスが挿入される）
- ホットキー化したい場合: `clip2file.cmd` のショートカットを作りデスクトップに置いて
  プロパティ→ショートカットキーに `Ctrl+Alt+V` 等を割り当て
- `clips\` は14日より古いものを起動時に自動掃除

## 通知まわり

- **トースト＆ライブボード状態更新は既存フックがそのまま効く**（`~\.claude\hooks\session_notify.py`。
  フックはフロントエンド非依存＝CLIでも拡張でも発火）
- 任意: `~\.claude\settings.json` に `"preferredNotifChannel": "terminal_bell"` を追加すると
  手待ち時にベルが鳴る（追加前にバックアップ推奨。自宅PCは2026-08-09に追加済み）
- **タブを光らせたい場合**（任意）: Windows Terminal の 設定 → プロファイル既定値 →
  詳細 → ベル通知スタイル で「ウィンドウのフラッシュ」「タスクバーのフラッシュ」をON
  （音はトーストと二重になるのでOFFでも可）
- **タブの待ちリング**（2026-08-10 会社S16）: フックが OSC 9;4 をタブのコンソールに直書き＝
  **手待ち（待機中/許可待ち）のタブにリングが出て、入力を始めると消える**。
  「どのタブがあなた待ちか」がタブ一覧で一目。WTタブ限定（Cursorはコンソール無し＝自動スキップ）。
  ⚠️ 現行WTはタブ内リングを状態色で塗り分けない（アクセント色固定・2026-08-10実測）＝
  🟡/🔴の色区別はトースト/ライブボード担当。シーケンス自体は state 2/4 を送っているので
  WT側が対応したら自動で色が付く。
  動作確認（PowerShell）: WTタブ内で
  `python "$env:USERPROFILE\.claude\hooks\session_notify.py" --test-osc waiting_permission`
  （消す: `--test-osc working`）

## タブアイコン（wt_profile・2026-08-10 会社S16）

タブは powershell 起動なので素のままだと**PSの青アイコン**になる。`workspaces.json` の
`wt_profile` にWTプロファイル名を書くと、全ランチャー（launch / open_session / restore_tabs）が
`wt new-tab -p <名前>` で起動＝タブアイコンを差し替えられる:

1. WT設定（settings.json）にプロファイル「claude」を追加
   （icon=`%USERPROFILE%\.claude\hooks\toast_icons\agentops.ico`＝トーストと同じ信号機アイコン。
   会社PCは 2026-08-10 追加済み・バックアップ `settings.json.bak-20260810-agentops`）
2. `workspaces.json` に `"wt_profile": "claude"` （空なら従来通り＝デフォルトプロファイル）

## 注意・制約

- グローバル既定モデルは `opus`（settings.json）。タブ起動後 `/model` で確認・変更
- タブタイトルは起動時固定（セッション名で動的に変わる機能はCC側に未実装・要望issue開放中）
- **ウインドウを閉じるとセッションのプロセスも終わる**。翌朝 `.\launch.ps1 -Continue` で復帰
- `-NoExit` 付きで起動するので claude を `/exit` してもタブは残る（そのまま `claude -c` 等を打てる）

## 戻し方

- ベル通知をやめる: `~\.claude\settings.json` から `"preferredNotifChannel"` の行を削除
  （または `settings.json.bak-20260809` を書き戻す）
- ツール自体はこのフォルダを消すだけ（フック・履歴には何も仕込んでいない）
