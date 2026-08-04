#!/usr/bin/env python3
"""日次メンテ（S9）— タスクスケジューラから無人実行する想定。

やること（順に・各ステップ独立して失敗を切り分け）:
  1. ダッシュボード再生成（claude_dashboard_gen.py）… 朝開いた時点で最新にしておく
  2. config.local.json 世代バックアップ（backup_config.py）… 消失対策を日次で自動化
  3. 実行ログを reports/maintenance.log に追記（成功/失敗と所要時間）

無人実行なのでブラウザは開かない。1ステップが落ちても残りは続行し、
最後に全体の成否を終了コードに反映（1つでも失敗なら 1）。

手動確認: python daily_maintenance.py
"""
import datetime
import subprocess
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

REPO = Path(__file__).resolve().parent
LOG = REPO / "reports" / "maintenance.log"
PY = sys.executable

# ─── 委託台帳への配線（S9・最初の実バッチ配線＝ドッグフーディング）───────────
# この日次メンテは無人・サイレントに走るローカルバッチ＝委託台帳が本来狙う対象そのもの。
# 台帳に載せると live_board の「⏳委託中」に出て、朝メンテが死んだ日に気づける。
# 原則: 台帳のためにバッチを絶対に殺さない（import 失敗→None・呼び出しは握りつぶし）。
sys.path.insert(0, str(REPO / "agent-graph"))
try:
    import agent_job
except Exception:
    agent_job = None

JOB_ID = "daily-maintenance"


def _job(method, *args, **kwargs):
    """台帳呼び出しの握りつぶしラッパー。台帳の失敗でバッチを殺さない。"""
    if agent_job is None:
        return None
    try:
        return getattr(agent_job, method)(*args, **kwargs)
    except Exception:
        return None


STEPS = [
    ("dashboard", [PY, str(REPO / "claude_dashboard_gen.py")]),
    ("track_record", [PY, str(REPO / "track_record_gen.py"), "--no-open"]),
    ("backup", [PY, str(REPO / "backup_config.py")]),
]


def run_step(name, cmd):
    started = datetime.datetime.now()
    try:
        r = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=300)
        ok = r.returncode == 0
        tail = (r.stdout or "").strip().splitlines()[-1:] or [""]
        detail = tail[0] if ok else (r.stderr or r.stdout or "").strip().splitlines()[-1:][0] if (r.stderr or r.stdout).strip() else ""
    except subprocess.TimeoutExpired:
        ok, detail = False, "timeout(300s)"
    except Exception as e:  # 実行自体が起動できない等
        ok, detail = False, f"{type(e).__name__}: {e}"
    elapsed = (datetime.datetime.now() - started).total_seconds()
    return ok, elapsed, detail


def main():
    LOG.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 台帳: 開始（依頼元=この運用基盤 / 実行体=タスクスケジューラ）
    _job("start", title="日次メンテ（ダッシュボード再生成＋configバックアップ）",
         project="エージェント運用基盤", agent="task-scheduler",
         total=len(STEPS), job_id=JOB_ID, stale_sec=600)
    lines = []
    all_ok = True
    failed_detail = ""
    try:
        for i, (name, cmd) in enumerate(STEPS, 1):
            ok, elapsed, detail = run_step(name, cmd)
            all_ok = all_ok and ok
            if not ok and not failed_detail:
                failed_detail = f"{name}: {detail}"
            mark = "OK" if ok else "FAIL"
            line = f"[{stamp}] {name:10s} {mark:4s} {elapsed:5.1f}s  {detail}"
            lines.append(line)
            print(line)
            _job("progress", JOB_ID, cur=i, note=f"{name} {mark}")  # 呼ぶこと自体が heartbeat

        summary = f"[{stamp}] === {'ALL OK' if all_ok else 'HAD FAILURES'} ==="
        print(summary)
        with LOG.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n" + summary + "\n")

        if all_ok:
            _job("done", JOB_ID, summary=f"{len(STEPS)}ステップ完了")
        else:
            _job("fail", JOB_ID, summary=failed_detail or "1つ以上のステップが失敗")
        return 0 if all_ok else 1
    except BaseException as e:  # Ctrl+C 等でも running を残置しない
        _job("fail", JOB_ID, summary=f"{type(e).__name__}: {e}")
        raise


if __name__ == "__main__":
    sys.exit(main())
