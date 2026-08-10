# SETUP — Claude Code 向けセットアップ指示書

> **このファイルの読み手＝セットアップ先PCの Claude Code**。
> ユーザーから「このzipを展開してセットアップして」と渡される想定。
> 以下を上から実行すれば、この環境で `claude-session-visualizer` が動く状態になる。
> 不明点（採番規約・案件マッピング等、この環境固有の情報）はユーザーに聞くこと。

## 0. これは何か

`~/.claude` に溜まる Claude Code のセッション（トランスクリプト jsonl）を可視化するローカルツール。主な機能:

- **ライブボード**（`live_board.py`）: 並行セッションの「今どれが🟢作業中/🟡待機中/🔴許可待ちか」＋休眠セッションの再開予定/再開候補。ターミナル/ブラウザ両対応。
- **セッション地図**（`claude_dashboard_gen.py` / `l0_report.py`）: 採番規約（例「〇〇 S3 開始」）で「どの案件に何セッション/いつ/どこまで」を集計。

設計は **汎用コア（コード）／個人設定（`config.local.json`）分離**。コードは触らず、原則 `config.local.json` を書くだけでこの環境に適応する。

## 1. 動作前提（この配布物の対象環境）

- **OS: Windows** / **Claude Code: Cursor 拡張版**（この前提で作られている。素のCLIやMacは §6 参照）
- **Python 3.8+** がPATHにあること（`python --version` で確認）。外部ライブラリ不要（標準ライブラリのみ）。
- Claude Code のデータが `~/.claude/projects/*/*.jsonl` に溜まっていること（＝この環境で普段からClaude Codeを使っている）。

## 2. 配置

zipを好きな場所に展開する（例 `C:\source\tools\claude-session-visualizer` や `%USERPROFILE%\claude-session-visualizer`）。パスは `%~dp0`/`__file__` 基準で解決するので**どこでも動く**。

## 3. config.local.json を作る（必須・この環境固有）

`config.example.json` を **`config.local.json` にコピー**して編集する（`config.local.*` は `.gitignore` 済＝個人設定でありコミットされない）。

```
copy config.example.json config.local.json
```

編集ポイント（最初は最小限でOK。埋めるほど案件マッピング精度が上がる）:

| キー | 何を書くか | 最小 |
|---|---|---|
| `claude_dir` | 通常 `~/.claude` のままでOK | そのまま |
| `workspaces` | `[]`（空＝全ワークスペース走査） | `[]` |
| `workspace_labels` | `~/.claude/projects/` 配下のディレクトリ名 → 表示名。例 `"c--source-myrepo": "MyRepo"`。**ディレクトリ名の作り方**＝そのワークスペースの絶対パスの英数字以外を全部 `-` に置換したもの（`~/.claude/projects/` を実際に `ls` すれば分かる） | `{}` |
| `kickoff_patterns` | セッション採番の合言葉の正規表現。**サンプルのまま**で「〇〇 S3 開始」「〇〇 新セッション開始」を拾える。独自の言い回しがあれば追加 | そのまま |
| `project_aliases` | 案件の別名→正式名の辞書。例 `{"本命案件": ["別名1","略称"]}` | `{}` |
| `title_hints` | 採番していない（AIタイトルだけの）セッションを案件に紐付けるキーワード。**配列順＝優先度（先勝ち）**。具体案件を上・一般語を下に | `[]` |
| `workspace_hints` | 「このリポジトリ＝この案件」の固定割り当て | `{}` |
| `pinned_sessions` / `dormant_*` | 休眠セッション管理用。**デフォルトのままでOK**（`pinned_sessions:[]`, `dormant_lookback_days:10`, `dormant_lookback_days_unnumbered:30`, `dormant_suggest_max:8`） | そのまま |

> **まず動かす → 未分類を見て `title_hints`/`project_aliases` を育てる**、の順が楽。`python l0_report.py` を実行すると未分類セッションの本文スニペットが出るので、それを見てキーワードを足していく。

**ユーザーに確認すべきこと**: この環境でセッション採番の合言葉を使っているか（「〇〇 S3 開始」のような）／主要な案件名・リポジトリ。分からなければ空のまま動かして後から育てる。

> 📎 **採番運用について**: 照準①（案件×セッション**採番**地図）を活かすには「〈案件名〉 S〈n〉 開始」で始める運用が要る。その"型"と、案件ごとの台帳から前回の続きを復元する自動化（スキル化）の作り方を **`採番セッション運用ガイド.md`**（同梱）に機密ゼロでまとめてある。採番地図まで使いたいなら先にそれを読む。使わないならこの節は無視でOK（ライブボード・休眠管理は採番なしで動く）。

## 4. 状態表示フックの配備（任意・状態の精度を上げる）

ライブボードの🟢🟡🔴判定は **①フック状態（主軸）＋②jsonl更新時刻（フォールバック）** の二段構え。
**フックを入れなくても** jsonl mtime で🟢作業中/🟡待機中は出る。**🔴許可待ちの区別**と、長ターン中の🟢維持精度を上げたいならフックを配備する。

配備手順:

1. `hooks/session_notify.py` を `~/.claude/hooks/` にコピー
   ```
   copy hooks\session_notify.py "%USERPROFILE%\.claude\hooks\session_notify.py"
   ```
2. `~/.claude/settings.json` の `hooks` に、**Stop / Notification / UserPromptSubmit** の3イベントで下記コマンドを追加（`type:"command"`）。**この環境のユーザー名の絶対パス**にすること:
   ```
   python "%USERPROFILE%\.claude\hooks\session_notify.py"
   ```
   （settings.json は環境変数展開しない場合があるので、実パス `C:\Users\<name>\.claude\hooks\session_notify.py` に書くのが確実。既存の hooks 配列がある場合は要素として足す＝既存フックを消さない。）

3. フックはホットリロード＝settings保存で既存タブにも即反映。状態JSONは `~/.claude/hooks/session_state/<session_id>.json` に出る（working/waiting_input/waiting_permission・7日超は自動削除）。

4. **（任意）トーストの名義・アイコンを自前化**: `hooks/register_toast_app.ps1` を実行すると、AUMID `goof.AgentOps` を HKCU に登録（管理者不要・`unregister_toast_app.ps1` で完全に戻せる）。トーストが「Windows PowerShell」名義ではなく**自前の名前＋信号機アイコン**になり、クリックでライブボードHTMLが開くようになる（`~/.claude/hooks/toast_click.txt` にURI）。未登録でもトースト自体は出る（PowerShell名義フォールバック）。
   - トーストには**状態色の丸アイコン**（🟡待機中 `#fab219`／🔴許可待ち `#d03b3b`＝ライブボードと同一hex）が付く。アイコンは `session_notify.py --make-icons` が純Pythonで `~/.claude/hooks/toast_icons/` に自己生成（Pillow不要・消しても再生成される）
   - 動作テスト: `python "%USERPROFILE%\.claude\hooks\session_notify.py" --test-toast waiting_permission`

5. **（任意）WTタブの待ちリング＆タブアイコン**: フックが OSC 9;4 をコンソール直書き＝Windows Terminal のタブに**手待ちリング**が出る（入力開始で消える・配備だけで有効・Cursorでは自動スキップ。※現行WTはリングを状態色で塗り分けないため色区別はトースト/ボード担当）。タブアイコンを信号機化したい場合は WT設定にプロファイル「claude」を追加して `claude-wt/workspaces.json` の `wt_profile` に設定（詳細は `claude-wt/README.md`）。

> ⚠️ **重要（§6の地雷1）**: Cursor拡張版CCは **PowerShell の `-File` フックを実行しない**。必ず **python フック**（`session_notify.py`）を使う。`.ps1` 版（`session_notify.ps1`）は同梱しているが**CLI専用・Cursorでは非実行**。トースト通知は python から powershell 子プロセスを spawn する形で内部的に出す。

## 5. 使い方

```
:: ライブボード（ブラウザ・10秒自動更新）。停止＝最小化されたコンソール窓を閉じる
live_board.bat

:: ライブボードをターミナルに1回表示 / 監視
python live_board.py
python live_board.py --watch 10

:: セッション地図（静的ダッシュボード）
claude_dashboard.bat
python l0_report.py            :: 未分類の本文スニペット付き棚卸し（title_hints育成に使う）

:: 休眠セッション管理
python live_board.py pins                       :: ピン一覧
python live_board.py pin <sid接頭辞> [メモ]      :: 再開予定にピン留め（sid接頭辞は8文字程度でOK）
python live_board.py unpin <sid接頭辞>
python live_board.py candidates [--days N]      :: 再開候補を遡って全一覧（番号なし単独も）
```

ライブボード下部の休眠セクション:
- **📌 再開予定** … ピン済み・プロセス停止中（後で `--resume` する意図を明示したもの）
- **💤 再開候補・番号付き** … 案件の最新Sのみ（稼働中/新しいSがあれば旧番号は自動除外）
- **💤 再開候補・番号なし単独** … 採番で追えず埋もれやすいので長め（30日）に遡って提示

## 6. ハマりどころ集（先人の知見・Windows+Cursor前提）

1. **PowerShell `-File` フックはCursor拡張版CCで発火しない**（python は動く）。状態フックは必ず python。※真因は「powershell vs python」で「sync vs async」ではない（心拍ログで確定済）。
2. **多重watcher事故**: `live_board.py --watch` を複数起動すると、古いプロセスが古いHTMLで上書きし返す。→ 起動時に `_kill_stale_watchers`（Windows専用）で他の watcher を掃除する仕組みが入っている。**掃除が走るのは `--watch` 起動時のみ**。
3. **コード変更が反映されない**: 常駐 watcher は**起動時のソースをメモリ保持**する。`live_board.py` を編集したら **watcher を再起動**しないと反映されない（HTML一発生成だけだと古い watcher に即上書きされ「変わってない」と誤認する）。
4. **Cursorのウィンドウ復元で幽霊セッションが出る**: Cursorは復元時に1タブあたり claude.exe を2個起動する（fresh捨てプロセス→`--resume`本体）。fresh側は会話ゼロで jsonl 未生成のまま `claude agents --json` に残る。→ ライブボードは **transcript(jsonl)実在フィルタ**で除外し、件数だけ脚注表示する（対処済み）。
5. **`--resume` の挙動**: resume は新sidを振らず**同一 jsonl に同一 sessionId で追記**する（実証済）。だから sid をキーにしたピン・採番マッピングは resume 跨ぎで安定。サブエージェント記録 `agent-*.jsonl` は親のsidを持つので集計時は除外（コードは対応済）。
6. **文字化けの偽陽性**: Windows既定CP932で UTF-8 の state JSON を `python -m json.tool` 等で読むと化けて見えるが、ファイル自体は正しいUTF-8。検証は生バイトで。

### 素のCLI / Mac・Linux に移す場合（この配布物の対象外）

- 素のターミナルCLIなら §6-1,4 のCursor固有問題は無い（が §6-2,3 の多重watcher/再起動は共通）。
- **Mac/Linux**: `_kill_stale_watchers` は Windows専用（`os.name != "nt"` で no-op になる）。トースト通知も powershell 依存なので発火しない（状態JSON書き込み自体は動く）。パス区切りは pathlib で吸収されるが、`.bat` は使えないので `python live_board.py --html --watch 10` を直接叩く。必要なら移植をユーザーに提案する。

## 6.5 定期自動メンテのタスク登録（S9・任意）

`daily_maintenance.py`（ダッシュボード再生成＋config世代バックアップ＋ログ）を平日朝に無人実行する Windows タスク。`pythonw.exe` のパスは各環境に合わせて差し替える（`(Get-Command python).Source` の隣の `pythonw.exe`）。

```powershell
$repo = "C:\source\git-h.fukuda1207\claude-session-visualizer"   # 自環境のリポパス
$pyw  = Join-Path (Split-Path (Get-Command python).Source) "pythonw.exe"
$action  = New-ScheduledTaskAction -Execute $pyw -Argument "daily_maintenance.py" -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 8:30am
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName "AgentOps-DailyMaintenance" -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force
```

- `StartWhenAvailable` … PC停止/スリープで見逃した日は起動後に遅延実行。
- `LogonType Interactive` … ユーザープロファイル（`~/.claude`）へのアクセスが要るのでログオン時実行。
- 解除: `Unregister-ScheduledTask -TaskName AgentOps-DailyMaintenance -Confirm:$false`

## 7. この配布物に含まれないもの（意図的除外）

- `config.local.json`（元PCの個人設定・案件マッピング＝機密）→ この環境用に §3 で新規作成する。
- `docs/`（元PCの運用台帳＝機密）、`reports/` `cache/`（生成物・トランスクリプト由来）。
- ※ `config.example.json` と一部コードコメント／README に、仕組み説明用の案件名が例示として残っている（実データではない）。完全に汎用化したい場合はそれらをジェネリックな語に置換してよい。
