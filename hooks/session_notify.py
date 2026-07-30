"""Claude Code hook: session state JSON + Windows toast (Python port).

WHY PYTHON (S6 2026-07-30): Cursor's Claude Code extension does NOT execute
PowerShell `-File` command hooks (sync OR async) -- proven by a heartbeat that
never fires -- while it DOES execute `python` command hooks (english_voice.py
works and receives stdin). So the original session_notify.ps1 never ran under
Cursor. This port runs as a python hook so state JSON is written under Cursor
too; the Windows toast is still produced by spawning a detached PowerShell child
(python-spawns-powershell works; Cursor-runs-powershell-as-a-hook does not).

Events wired in settings.json (all call this file with `python`):
  UserPromptSubmit -> state only ("working")
  Stop             -> state ("waiting_input") + toast
  Notification     -> state ("waiting_permission" / "waiting_input") + toast

State files (live board data source, read by live_board.py):
  <this dir>/session_state/<session_id>.json
ASCII-only source (Japanese arrives via stdin/data). Never breaks Claude Code:
any error is swallowed and the process exits 0.
"""
import json
import os
import subprocess
import sys
import time

HOOK_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(HOOK_DIR, "session_state")
PRUNE_DAYS = 7
FULL_POWERSHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"


def read_hook_input():
    raw = sys.stdin.buffer.read()
    raw = raw.lstrip(b"\xef\xbb\xbf \t\r\n")  # tolerate a leading BOM/whitespace
    if not raw:
        return None
    return json.loads(raw.decode("utf-8", "replace"))


def resolve_title(transcript_path, prompt, cwd):
    """First real user message in the transcript, else the prompt, else cwd."""
    title = ""
    if transcript_path and os.path.exists(transcript_path):
        try:
            with open(transcript_path, encoding="utf-8") as f:
                for i, line in enumerate(f):
                    if i >= 80 or title:
                        break
                    line = line.strip()
                    if '"type":"user"' not in line.replace(" ", ""):
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    content = obj.get("message", {}).get("content", "")
                    text = ""
                    if isinstance(content, str):
                        text = content
                    elif isinstance(content, list):
                        for c in content:
                            if isinstance(c, dict) and c.get("type") == "text":
                                text = c.get("text", "")
                                break
                    text = " ".join(text.split()).strip()
                    # skip system-ish / command wrapper messages
                    if text and not text.startswith("<") and len(text) > 1:
                        title = text[:60] + "..." if len(text) > 60 else text
        except OSError:
            pass
    if not title and prompt:
        p = " ".join(prompt.split()).strip()
        title = p[:60] + "..." if len(p) > 60 else p
    if not title:
        title = os.path.basename(cwd.rstrip("\\/")) if cwd else "Claude Code"
    return title


def write_state(session_id, state, event, cwd, transcript, title, msg, prompt):
    os.makedirs(STATE_DIR, exist_ok=True)
    last_prompt = ""
    if prompt:
        last_prompt = " ".join(prompt.split()).strip()
        if len(last_prompt) > 120:
            last_prompt = last_prompt[:120] + "..."
    obj = {
        "session_id": session_id,
        "state": state,
        "event": event,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cwd": cwd,
        "transcript_path": transcript,
        "title": title,
        "message": msg,
        "last_prompt": last_prompt,
    }
    path = os.path.join(STATE_DIR, session_id + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    prune_old()


def prune_old():
    cutoff = time.time() - PRUNE_DAYS * 86400
    try:
        for name in os.listdir(STATE_DIR):
            if not name.endswith(".json"):
                continue
            p = os.path.join(STATE_DIR, name)
            try:
                if os.path.getmtime(p) < cutoff:
                    os.remove(p)
            except OSError:
                pass
    except OSError:
        pass


def show_toast(head, body):
    """Spawn a detached PowerShell child to raise a WinRT toast.
    Text is passed via env vars so Japanese/quotes need no escaping."""
    ps = (
        "[Windows.UI.Notifications.ToastNotificationManager,"
        " Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument,"
        " ContentType = WindowsRuntime] | Out-Null;"
        "$xml = [Windows.UI.Notifications.ToastNotificationManager]::"
        "GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]"
        "::ToastText02);"
        "$t = $xml.GetElementsByTagName('text');"
        "$t.Item(0).AppendChild($xml.CreateTextNode($env:TOAST_HEAD)) | Out-Null;"
        "$t.Item(1).AppendChild($xml.CreateTextNode($env:TOAST_BODY)) | Out-Null;"
        "$toast = New-Object Windows.UI.Notifications.ToastNotification($xml);"
        "$appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}"
        "\\WindowsPowerShell\\v1.0\\powershell.exe';"
        "[Windows.UI.Notifications.ToastNotificationManager]::"
        "CreateToastNotifier($appId).Show($toast)"
    )
    env = dict(os.environ, TOAST_HEAD=head, TOAST_BODY=body)
    exe = FULL_POWERSHELL if os.path.exists(FULL_POWERSHELL) else "powershell"
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        [exe, "-NoProfile", "-Command", ps], env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, creationflags=flags)


def main():
    hook = read_hook_input()
    if not hook:
        return
    event = str(hook.get("hook_event_name") or "Stop")
    session_id = str(hook.get("session_id") or "")
    transcript = str(hook.get("transcript_path") or "")
    cwd = str(hook.get("cwd") or "")
    msg = str(hook.get("message") or "")       # Notification only
    prompt = str(hook.get("prompt") or "")     # UserPromptSubmit only

    title = resolve_title(transcript, prompt, cwd)

    if session_id:
        if event == "UserPromptSubmit":
            state = "working"
        elif event == "Notification":
            state = "waiting_permission" if "permission" in msg else "waiting_input"
        else:
            state = "waiting_input"
        write_state(session_id, state, event, cwd, transcript, title, msg, prompt)

    # toast only for Stop / Notification (UserPromptSubmit is state-only)
    if event == "UserPromptSubmit":
        return
    if event == "Notification":
        head = "Claude Code: needs attention"
        body = (title + "\n" + msg) if msg else title
    else:
        head = "Claude Code: waiting for input"
        body = title
    show_toast(head, body)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # never break Claude Code because of a hook
        pass
    sys.exit(0)
