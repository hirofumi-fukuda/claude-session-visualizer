# claude-session-visualizer

`~/.claude` に蓄積される Claude Code のセッションデータ（トランスクリプト jsonl・メモリ・タスク・統計）を可視化するローカルツール。

## 見える化の照準

1. **案件×セッション地図** — セッション採番規約（例:「imazine S8 開始」）を使い、どの案件に何セッション/いつ/どこまで進んだかを一覧化
2. **並行セッションの今** — 複数セッション並行時のライブ監視（今どれが何をしている・どこまで進んだ）
3. **エージェントの中身** — セッション内のサブエージェント/ワークフローのやりとり・ツール呼び出しの会話ビューア
4. **利用統計** — トークン/セッション数/時間帯の使用量

> 🆕 **委託ジョブの今**（ギルド運用一式で追加）— セッションとして見えないバッチ（ローカルLLM等）を `agent_job` 台帳経由でライブボードに「⏳委託中」表示。死んだジョブに気づけるのが価値。→ [docs/guild/03-委託台帳ガイド.md](docs/guild/03-委託台帳ガイド.md)

## 使い方（日常運用）

- **`claude_dashboard.bat`** … 案件×セッション地図の静的ダッシュボードを再生成→ブラウザ表示（見たいときにダブルクリック・数秒）
- **`live_board.bat`** … 並行セッションのライブボード（ブラウザ10秒自動更新・停止=最小化窓を閉じる）
- **`counter.bat` / `python counter.py [案件名]`** … エージェントカウンター（台帳の近況・依頼票📨・回覧板の窓口。案件名を渡すとその案件宛ての未消化📨だけ返す）
- **`backup_config.bat` / `python backup_config.py`** … `config.local.json`（gitignore対象＝案件マッピング/ピン情報/副業ラベルの正本）を `~/.claude/backups/agent-ops/` へ世代バックアップ（既定10世代・内容差分でスキップ・JSON検証付き）。復元=`python backup_config.py --restore latest`／一覧=`--list`。会社OneDriveは副業ラベルを含むため使わない（個人ルール）
- CLI: `python l0_report.py`（Markdown棚卸し・未分類の本文スニペット付き）／`python claude_dashboard_gen.py`／`python live_board.py [--watch N] [--html]`／`python session_no.py "<案件名>"`（次のS番号を即答）
- **休眠セッション管理**（タブを閉じたが後で `--resume` して続けたいセッション）: ライブボード下部に **📌再開予定**（ピン済み・停止中）と **💤再開候補**（自動提示）が出る。候補は2グループ:
  - **番号付き** … 案件の**最新Sのみ**（同案件に新しいS／稼働中Sがあれば旧番号は自動除外。短めの遡り＝`dormant_lookback_days`）
  - **番号なし単独** … 採番で追えず埋もれやすいので長めに遡る（`dormant_lookback_days_unnumbered`）
  - ピン操作: `pin <sid接頭辞> [メモ]`（sid接頭辞は8文字程度でOK）／`unpin <sid接頭辞>`／`pins`（一覧）
  - もっと遡って確認: `python live_board.py candidates [--days N]` … 案件マップ済・停止中・未ピンを番号なし含め遡って全一覧（ボードの抑制＝最新Sのみ/短遡り を外した全表示）
  - ピンは `config.local.json` の `pinned_sessions[]` に保存。sid は `--resume` 跨ぎで安定（同一 jsonl に同一 sid で追記される）ので迷子にならない。「休眠 vs クローズ」はデータ上区別不能（どちらもプロセス死）＝区別は人の意図＝ピンが真実、候補は自動提示の補助網
  - ⚠️ ライブボードは**常駐watcherが起動時のコードをメモリ保持**するため、コード変更後は watcher を再起動しないと反映されない（`--watch` 起動時のみ多重watcher掃除が走る）

## 定期自動メンテ（S9・タスクスケジューラ）

- `daily_maintenance.py` … 無人実行のオーケストレータ。①ダッシュボード再生成 ②`config.local.json` 世代バックアップ ③`reports/maintenance.log` に成否ログ。ブラウザは開かない・1ステップ落ちても続行・全成否を終了コードに反映。
- Windows タスク `AgentOps-DailyMaintenance` … **平日 8:30** に `pythonw.exe daily_maintenance.py` を無ウィンドウ実行（`StartWhenAvailable`＝PC停止/スリープで見逃した日は起動後に遅延実行）。
  - 状態確認: `Get-ScheduledTaskInfo -TaskName AgentOps-DailyMaintenance`（`LastTaskResult` 0=成功）
  - 手動実行: `Start-ScheduledTask -TaskName AgentOps-DailyMaintenance` または `python daily_maintenance.py`
  - 解除: `Unregister-ScheduledTask -TaskName AgentOps-DailyMaintenance -Confirm:$false`
  - 再登録スクリプトは `SETUP.md` 参照（環境依存＝pythonw のパスを各自の環境に合わせる）。

## ロードマップ

- **L0** ✅ 棚卸しレポート（`l0_report.py`・2026-07-29）
- **L1** ✅ 静的ダッシュボード（`claude_dashboard_gen.py` → HTML＋JSON中間出力・2026-07-29）
- **L2** ライブ監視 → **自作Webアプリは廃止**（S2判断: claude-view=macOS専用。公式Agent View＋`live_board.py`〔`claude agents --json`×フック状態JSON×採番マッピング・静的HTML＋メタリフレッシュ〕で充足）。会話ビューアは既存OSS（claude-code-history-viewer 等・未導入）
- **L3** 業務ダッシュボードとの統合（`reports/claude_dashboard_data.json` が受け口・未着手）

## 設計原則

- 🔒 **ローカル完結**: トランスクリプトは機密を含む。データを外部に送信しない。リポジトリにはコードのみ（データ・生成レポートはコミットしない）
- 📖 **読み取り専用**: `~/.claude` 配下は read-only で扱う（Claude Code の稼働データを壊さない）
- 🔧 **汎用コアと個人設定の分離**: パーサー/ビューア＝汎用。セッション採番規約・案件マッピング＝設定ファイルに外出し（将来の社内展開時は各自が自分の規約を書くだけにする）
- ⚡ 大容量対策: 数百MBの jsonl を毎回全パースしない（mtime差分・キャッシュ）
- 📒 正本は台帳（auto-memory）のまま。ビューアは毎回ファイルから描画（内容のコピー保持＝二重管理は禁止）

## 導入

`SETUP.md` を参照。`config.example.json` → `config.local.json` を自環境で作成し、`hooks/session_notify.py` を `~/.claude/hooks/` に配線する。claude.exe がPATHに無い環境（IDE拡張版）でも `_claude_cli()` が拡張ディレクトリを自動探索する（見つからない場合のみ `config.local.json` の `claude_cli` に絶対パス）。

## ギルド運用一式（体制の型）

1人のユーザー＋複数AIエージェントで回す運用の「型」と道具3点セット（ボード＝いま／マップ＝ぜんたい／カウンター＝紙）。逆輸出パッケージ由来。

- [docs/guild/01-ギルドの仕組み.md](docs/guild/01-ギルドの仕組み.md) — 体制の型（司令塔×トラック×実行部隊）・通信4経路・実運用の学び
- [docs/guild/02-台帳と依頼票の規約.md](docs/guild/02-台帳と依頼票の規約.md) — 台帳の標準6セクション・依頼票/回覧板・採番運用
- [docs/guild/03-委託台帳ガイド.md](docs/guild/03-委託台帳ガイド.md) — `agent_job` 委託台帳の使い方・配線の地雷集
- [採番セッション運用ガイド.md](採番セッション運用ガイド.md) — 採番運用の"型"とスキル化の作り方

## 台帳・プロジェクト管理

- 現在地・セッション履歴: [docs/00-現在地.md](docs/00-現在地.md)
- 起動合言葉: 「エージェント運用基盤 S〈n〉 開始」（旧「セッション見える化」・S8で改名・番号引き継ぎ）
