#!/usr/bin/env python3
"""config.local.json 世代バックアップ（S9）。

なぜ必要か: config.local.json は gitignore 対象（副業ラベル・顧客案件マッピングを
含むため版管理に載せられない）。S3-S7 で育った回収タクソノミ＋S7 のピン情報がここに
のみ存在し、誤削除・事故編集・`git clean -fdx` で失う消失リスクがあった。

方針:
  - バックアップ先は **repo外の個人領域**（既定 `~/.claude/backups/agent-ops/`）。
    repo ツリー外なので repo 削除/クリーンの巻き添えを受けない。会社 OneDrive は
    副業ラベルを含むため使わない（個人ルール）。
  - **世代管理**（タイムスタンプ付き・既定10世代）。事故編集からのロールバック用。
  - バックアップ前に **JSON パース検証**（壊れたファイルは退避しない）。
  - **内容ハッシュ差分**: 直近バックアップと同一内容ならスキップ（毎日回しても増えない）。

使い方:
  python backup_config.py                 # 既定パスをバックアップ
  python backup_config.py --list          # 世代一覧
  python backup_config.py --keep 20       # 保持世代数を変更
  python backup_config.py --restore latest   # 最新世代を config.local.json へ復元
  python backup_config.py --restore <ファイル名>  # 指定世代を復元（復元前に現物も自動退避）
"""
import argparse
import datetime
import hashlib
import json
import shutil
import sys
from pathlib import Path

# Windows コンソールは既定 CP932。日本語＋記号(✓✗=)を安全に出すため UTF-8 化。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

REPO = Path(__file__).resolve().parent
DEFAULT_SRC = REPO / "config.local.json"
DEFAULT_BACKUP_DIR = Path.home() / ".claude" / "backups" / "agent-ops"
DEFAULT_KEEP = 10
PREFIX = "config.local."
SUFFIX = ".json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshots(backup_dir: Path):
    """新しい順のバックアップ一覧（タイムスタンプ命名なので名前降順＝時系列降順）。"""
    if not backup_dir.exists():
        return []
    snaps = [p for p in backup_dir.iterdir()
             if p.is_file() and p.name.startswith(PREFIX) and p.name.endswith(SUFFIX)
             and p.name != DEFAULT_SRC.name]
    return sorted(snaps, key=lambda p: p.name, reverse=True)


def do_backup(src: Path, backup_dir: Path, keep: int) -> int:
    if not src.exists():
        print(f"✗ ソースが無い: {src}", file=sys.stderr)
        return 1
    try:
        json.loads(src.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f"✗ JSON として不正なのでバックアップ中止: {e}", file=sys.stderr)
        return 1

    backup_dir.mkdir(parents=True, exist_ok=True)
    snaps = _snapshots(backup_dir)
    src_hash = _sha(src)
    if snaps and _sha(snaps[0]) == src_hash:
        print(f"= 変更なし（直近 {snaps[0].name} と同一）。スキップ。")
        return 0

    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = backup_dir / f"{PREFIX}{ts}{SUFFIX}"
    shutil.copy2(src, dest)
    print(f"✓ バックアップ作成: {dest}")

    # 世代プルーニング
    snaps = _snapshots(backup_dir)
    pruned = 0
    for old in snaps[keep:]:
        old.unlink()
        pruned += 1
    if pruned:
        print(f"  古い世代 {pruned} 件を削除（保持 {keep} 世代）")
    print(f"  現在 {min(len(snaps), keep)} 世代 @ {backup_dir}")
    return 0


def do_list(backup_dir: Path) -> int:
    snaps = _snapshots(backup_dir)
    if not snaps:
        print(f"（バックアップ無し @ {backup_dir}）")
        return 0
    print(f"世代一覧 @ {backup_dir}（新しい順）:")
    for p in snaps:
        size = p.stat().st_size
        print(f"  {p.name}  ({size:,} bytes)")
    return 0


def do_restore(src: Path, backup_dir: Path, which: str) -> int:
    snaps = _snapshots(backup_dir)
    if not snaps:
        print(f"✗ 復元元が無い @ {backup_dir}", file=sys.stderr)
        return 1
    if which == "latest":
        chosen = snaps[0]
    else:
        cands = [p for p in snaps if p.name == which or p.name == f"{PREFIX}{which}{SUFFIX}"]
        if not cands:
            print(f"✗ 該当世代が無い: {which}（--list で確認）", file=sys.stderr)
            return 1
        chosen = cands[0]

    # 復元前に現物を安全退避（復元自体が事故になった時のため）
    if src.exists():
        try:
            json.loads(src.read_text(encoding="utf-8"))
            do_backup(src, backup_dir, DEFAULT_KEEP)  # 現物が正常ならまず世代化
        except (json.JSONDecodeError, UnicodeDecodeError):
            broken = backup_dir / f"{PREFIX}BROKEN-{datetime.datetime.now():%Y%m%d-%H%M%S}{SUFFIX}"
            backup_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, broken)
            print(f"⚠ 現物が壊れていたので {broken.name} に退避")

    shutil.copy2(chosen, src)
    print(f"✓ 復元完了: {chosen.name} → {src}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="config.local.json 世代バックアップ")
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC, help="バックアップ対象（既定=repo config.local.json）")
    ap.add_argument("--dir", type=Path, default=DEFAULT_BACKUP_DIR, help="バックアップ保存先（既定=~/.claude/backups/agent-ops）")
    ap.add_argument("--keep", type=int, default=DEFAULT_KEEP, help="保持世代数（既定10）")
    ap.add_argument("--list", action="store_true", help="世代一覧を表示")
    ap.add_argument("--restore", metavar="WHICH", help="復元（latest または世代ファイル名/タイムスタンプ）")
    args = ap.parse_args()

    if args.list:
        return do_list(args.dir)
    if args.restore:
        return do_restore(args.src, args.dir, args.restore)
    return do_backup(args.src, args.dir, args.keep)


if __name__ == "__main__":
    sys.exit(main())
