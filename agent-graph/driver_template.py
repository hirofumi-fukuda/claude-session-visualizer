# -*- coding: utf-8 -*-
"""委託バッチへの agent_job 台帳配線テンプレ。

原則: 台帳のためにバッチを絶対に殺さない。
  - import 失敗 → None（台帳なしで走る）
  - 呼び出しは _job() 握りつぶしラッパー経由のみ
  - BaseException（Ctrl+C含む）でも fail を記録して running を残置しない

起動側 .bat には必ず `set PYTHONIOENCODING=utf-8` を焼き込むこと（cp932 起動だと
子プロセス出力の U+FFFD が print できずログ関数ごと死ぬ）。下の reconfigure は自衛の二段目。
"""
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# agent_job.py を置いた場所を import パスに足す（環境に合わせて変更）
sys.path.insert(0, r"<AGENT_JOB_DIR>")
try:
    import agent_job
except Exception:
    agent_job = None  # 台帳が無くてもバッチは走る

JOB_ID = "nightly-batch"  # バッチ種別ごとに固定＝1枠上書き運用


def _job(method, *args, **kwargs):
    """台帳呼び出しの握りつぶしラッパー。台帳の失敗でバッチを殺さない。"""
    if agent_job is None:
        return None
    try:
        return getattr(agent_job, method)(*args, **kwargs)
    except Exception:
        return None


def main():
    items = ["2026-01-01", "2026-01-02"]  # 処理対象（例）
    # stale_sec: 1件の処理が長いバッチは heartbeat 間隔の2〜3倍を明示（既定30分だと誤警報）
    _job("start", title="夜間バッチ", project="サンプル案件",
         agent="ollama:model-name", total=len(items), job_id=JOB_ID, stale_sec=5400)
    try:
        for i, item in enumerate(items, 1):
            # ... 本業の処理 ...
            _job("progress", JOB_ID, cur=i, note=str(item))  # 呼ぶこと自体が heartbeat
        _job("done", JOB_ID, summary=f"{len(items)}件完了")
    except BaseException as e:  # Ctrl+C でも running を残置しない
        _job("fail", JOB_ID, summary=f"{type(e).__name__}: {e}")
        raise


if __name__ == "__main__":
    main()
