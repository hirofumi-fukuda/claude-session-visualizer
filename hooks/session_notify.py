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
ASCII-only source (Japanese arrives via stdin/data or escape sequences). Never
breaks Claude Code: any error is swallowed and the process exits 0.

Toast branding (S16 2026-08-10): the toast now uses a self-built ToastGeneric
XML with a state-colored dot icon (appLogoOverride) whose colors MATCH the live
board (live_board.py STATE_HTML). If hooks/register_toast_app.ps1 registered
the "goof.AgentOps" AUMID (HKCU), the toast shows our own app name/icon instead
of "Windows PowerShell"; otherwise it falls back to the PowerShell AUMID, so
this file keeps working standalone on an unregistered machine. Clicking the
toast opens the URI in <this dir>/toast_click.txt when present (the register
script points it at the live board HTML). No per-session resume button on
purpose: toasted sessions are alive in some tab, and claude-wt resume has no
double-resume guard (only restore_tabs.ps1 has one).

CLI (manual/setup use, errors NOT swallowed):
  python session_notify.py --make-icons          regenerate toast_icons/
  python session_notify.py --test-toast [state]  raise a test toast, print errors
"""
import json
import math
import os
import re
import struct
import subprocess
import sys
import time
import zlib

HOOK_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(HOOK_DIR, "session_state")
PRUNE_DAYS = 7
FULL_POWERSHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"

# Toast identity: our own AUMID when registered (register_toast_app.ps1),
# else the stock PowerShell AUMID (shows as "Windows PowerShell").
TOAST_APPID = "goof.AgentOps"
PS_APPID = ("{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}"
            "\\WindowsPowerShell\\v1.0\\powershell.exe")

ICON_DIR = os.path.join(HOOK_DIR, "toast_icons")
CLICK_FILE = os.path.join(HOOK_DIR, "toast_click.txt")  # optional click-target URI

# State dot colors -- MUST match live_board.py STATE_HTML
# (working #0ca30c / waiting_input #fab219 / waiting_permission #d03b3b).
STATE_COLORS = {
    "working": (0x0C, 0xA3, 0x0C),
    "waiting_input": (0xFA, 0xB2, 0x19),
    "waiting_permission": (0xD0, 0x3B, 0x3B),
}
ICON_FILES = {
    "working": "dot_working.png",
    "waiting_input": "dot_waiting_input.png",
    "waiting_permission": "dot_waiting_permission.png",
}
APP_ICO = "agentops.ico"
_ICON_BG = (0x1E, 0x24, 0x30)  # app icon plate (dark slate, board-ish)

# Toast head labels, aligned with board wording (\u-escaped to keep ASCII source):
# HEAD_WAITING    = "(yellow dot) Taiki-chuu -- anata no nyuuryoku machi"
# HEAD_PERMISSION = "(red dot) Kyoka-machi -- shounin ga hitsuyou"
HEAD_WAITING = ("\\U0001f7e1 \\u5f85\\u6a5f\\u4e2d \\u2014 "
                "\\u3042\\u306a\\u305f\\u306e\\u5165\\u529b\\u5f85\\u3061")
HEAD_PERMISSION = ("\\U0001f534 \\u8a31\\u53ef\\u5f85\\u3061 \\u2014 "
                   "\\u627f\\u8a8d\\u304c\\u5fc5\\u8981")

# Environment tag blocks (IDE notices, system reminders, command wrappers) are
# stripped from user text before use -- same regex as claude_sv/parser.py.
# (This file must stay standalone: it runs from ~/.claude/hooks without the repo.)
_TAG_RE = re.compile(
    r"<(ide_opened_file|ide_selection|ide_diagnostics|system-reminder|"
    r"command-name|command-message|command-args|local-command-stdout|"
    r"local-command-caveat|task-notification)"
    r"[^>]*>.*?</\1>",
    re.DOTALL,
)

# Injected non-human user messages that must never become the session title.
_INJECTED_PREFIXES = ("Base directory for this skill:", "Launching skill:")


def read_hook_input():
    raw = sys.stdin.buffer.read()
    raw = raw.lstrip(b"\xef\xbb\xbf \t\r\n")  # tolerate a leading BOM/whitespace
    if not raw:
        return None
    return json.loads(raw.decode("utf-8", "replace"))


def _clean_user_text(text):
    """Strip environment tag blocks, collapse whitespace (parser-equivalent)."""
    return " ".join(_TAG_RE.sub("", text).split()).strip()


def _record_texts(content):
    """Yield every text item of a user record (str or list-of-parts content)."""
    if isinstance(content, str):
        yield content
    elif isinstance(content, list):
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                yield c.get("text", "")


def resolve_title(transcript_path, prompt, cwd):
    """First real user message in the transcript, else the prompt, else cwd.

    Fix (S10 2026-08-04): the old logic looked only at the FIRST text item of
    each user record and skipped anything starting with "<". Cursor prepends
    <ide_opened_file>/<system-reminder> items BEFORE the typed text, so the
    real prompt was discarded and the next plain-text record -- usually a
    skill expansion ("Base directory for this skill: ...") -- became the
    title. Now: scan ALL text items, strip tag blocks (parser-equivalent),
    skip isMeta records and injected skill texts.
    """
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
                    if obj.get("type") != "user" or obj.get("isMeta") \
                            or obj.get("isSidechain"):
                        continue
                    for raw in _record_texts(obj.get("message", {}).get("content", "")):
                        text = _clean_user_text(raw)
                        if (not text or len(text) <= 1 or text.startswith("<")
                                or text.startswith(_INJECTED_PREFIXES)):
                            continue
                        title = text[:60] + "..." if len(text) > 60 else text
                        break
        except OSError:
            pass
    if not title and prompt:
        p = _clean_user_text(prompt)
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


# ---------------------------------------------------------------- toast icons
# Tiny pure-stdlib PNG/ICO writers: no Pillow on the hook path, and the icons
# regenerate themselves if the folder is wiped (file stays standalone).

def _clamp01(v):
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


def _png_rgba(size, pixel):
    """Write a size x size RGBA PNG; pixel(x, y) -> (r, g, b, a)."""
    rows = []
    for y in range(size):
        row = bytearray(b"\x00")  # filter type 0
        for x in range(size):
            row.extend(pixel(x, y))
        rows.append(bytes(row))

    def chunk(tag, data):
        body = tag + data
        return (struct.pack(">I", len(data)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9))
            + chunk(b"IEND", b""))


def _dot_png(size, rgb):
    """Anti-aliased solid dot on transparent background (state badge)."""
    c = (size - 1) / 2.0
    rad, aa = size / 2.0 - 2.0, 1.5

    def pixel(x, y):
        a = _clamp01((rad - math.hypot(x - c, y - c)) / aa)
        return (rgb[0], rgb[1], rgb[2], int(a * 255))

    return _png_rgba(size, pixel)


def _app_png(size):
    """App identity icon: dark rounded plate + board traffic light (red/yellow/green)."""
    r, aa = size * 0.18, 2.0
    cy, drad = size * 0.5, size * 0.11
    dots = [(size * 0.26, STATE_COLORS["waiting_permission"]),
            (size * 0.50, STATE_COLORS["waiting_input"]),
            (size * 0.74, STATE_COLORS["working"])]

    def pixel(x, y):
        nx = min(max(x, r), size - 1 - r)
        ny = min(max(y, r), size - 1 - r)
        plate = _clamp01((r - math.hypot(x - nx, y - ny)) / aa)
        rgb = _ICON_BG
        for cx, col in dots:
            da = _clamp01((drad - math.hypot(x - cx, y - cy)) / aa)
            if da > 0.0:
                rgb = tuple(int(rgb[i] + (col[i] - rgb[i]) * da) for i in range(3))
        return (rgb[0], rgb[1], rgb[2], int(plate * 255))

    return _png_rgba(size, pixel)


def _ico_from_png(png, size):
    """Single-image ICO with a PNG payload (Vista+)."""
    b = 0 if size >= 256 else size
    entry = struct.pack("<BBBBHHII", b, b, 0, 0, 1, 32, len(png), 22)
    return struct.pack("<HHH", 0, 1, 1) + entry + png


def make_icons(force=False):
    """Generate toast_icons/ (state dots + app ico); returns written/present paths."""
    os.makedirs(ICON_DIR, exist_ok=True)
    out = []
    for state, name in ICON_FILES.items():
        p = os.path.join(ICON_DIR, name)
        if force or not os.path.exists(p):
            with open(p, "wb") as f:
                f.write(_dot_png(128, STATE_COLORS[state]))
        out.append(p)
    ico = os.path.join(ICON_DIR, APP_ICO)
    if force or not os.path.exists(ico):
        with open(ico, "wb") as f:
            f.write(_ico_from_png(_app_png(256), 256))
    out.append(ico)
    return out


def _state_icon(state):
    """Path of the dot icon for a state ("" if generation failed)."""
    try:
        make_icons()
        return os.path.join(ICON_DIR, ICON_FILES.get(state, ICON_FILES["waiting_input"]))
    except Exception:
        return ""


# ------------------------------------------------- WT tab progress dot (OSC 9;4)
# Windows Terminal renders OSC 9;4 as a colored dot in the tab + taskbar
# progress: state 2 = error/red, 4 = warning/yellow, 0 = clear. Mapping
# matches the live board: waiting_input -> yellow, waiting_permission -> red,
# working -> clear (Claude Code's own spinner takes over). WT-tab sessions
# only: under Cursor there is no console, so this silently no-ops. Unknown
# OSC sequences are ignored by other terminals (harmless).

_OSC_STATE = {"working": 0, "waiting_input": 4, "waiting_permission": 2}


def _console_progress(state):
    st = _OSC_STATE.get(state)
    if st is None or os.name != "nt":
        return False
    seq = "\x1b]9;4;%d;100\x07" % st if st else "\x1b]9;4;0;0\x07"
    try:
        return _write_conout(seq)
    except Exception:
        return False


def _write_conout(seq):
    """Write an escape sequence to the attached console via Win32.

    Two traps make the naive open("CONOUT$").write() unreliable:
    - a hook spawned without a console must AttachConsole to its parent's
      (Claude Code owns the WT tab's console);
    - the screen buffer may not have ENABLE_VIRTUAL_TERMINAL_PROCESSING set
      for our handle, in which case ESC bytes are printed as text instead of
      being parsed -> the OSC never reaches Windows Terminal. So: enable VT
      on the buffer for the write, then restore the previous mode.
    """
    import ctypes
    k32 = ctypes.windll.kernel32
    k32.CreateFileW.restype = ctypes.c_ssize_t
    GENERIC_RW = 0xC0000000
    SHARE_RW, OPEN_EXISTING, VT = 0x3, 0x3, 0x0004
    for attempt in (0, 1):
        h = k32.CreateFileW("CONOUT$", GENERIC_RW, SHARE_RW, None,
                            OPEN_EXISTING, 0, None)
        if h != -1:
            try:
                mode = ctypes.c_uint32()
                if not k32.GetConsoleMode(ctypes.c_ssize_t(h),
                                          ctypes.byref(mode)):
                    return False
                k32.SetConsoleMode(ctypes.c_ssize_t(h), mode.value | VT)
                n = ctypes.c_uint32()
                ok = k32.WriteConsoleW(ctypes.c_ssize_t(h), seq, len(seq),
                                       ctypes.byref(n), None)
                if not (mode.value & VT):
                    k32.SetConsoleMode(ctypes.c_ssize_t(h), mode.value)
                return bool(ok)
            finally:
                k32.CloseHandle(ctypes.c_ssize_t(h))
        if attempt or not k32.AttachConsole(-1):  # parent's console
            return False
    return False


# ---------------------------------------------------------------- toast raise

def _toast_appid():
    """Own AUMID when registered (HKCU), else stock PowerShell AUMID."""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"SOFTWARE\Classes\AppUserModelId" + "\\" + TOAST_APPID)
        winreg.CloseKey(key)
        return TOAST_APPID
    except OSError:
        return PS_APPID


def _click_uri():
    """Optional toast click target (single URI line in toast_click.txt)."""
    try:
        with open(CLICK_FILE, encoding="utf-8") as f:
            return f.readline().strip()
    except OSError:
        return ""


def _file_uri(path):
    return "file:///" + os.path.abspath(path).replace("\\", "/")


def _toast_xml(head, body, icon_path, launch_uri):
    """Self-built ToastGeneric XML (state dot as appLogoOverride)."""
    from xml.sax.saxutils import escape, quoteattr
    img = ""
    if icon_path and os.path.exists(icon_path):
        img = "<image placement=\"appLogoOverride\" src=%s/>" % quoteattr(
            _file_uri(icon_path))
    launch = ""
    if launch_uri:
        launch = " activationType=\"protocol\" launch=%s" % quoteattr(launch_uri)
    return ("<toast%s><visual><binding template=\"ToastGeneric\">%s"
            "<text>%s</text><text>%s</text></binding></visual></toast>"
            % (launch, img, escape(head), escape(body)))


def show_toast(head, body, state="waiting_input", debug=False):
    """Raise a WinRT toast via a detached PowerShell child.
    The whole XML rides in an env var: no quoting/escaping issues, and the
    PS side stays a dumb LoadXml+Show. debug=True runs synchronously and
    returns the PowerShell exit code (CLI --test-toast)."""
    xml = _toast_xml(head, body, _state_icon(state), _click_uri())
    ps = (
        "[Windows.UI.Notifications.ToastNotificationManager,"
        " Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument,"
        " ContentType = WindowsRuntime] | Out-Null;"
        "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument;"
        "$xml.LoadXml($env:TOAST_XML);"
        "$toast = New-Object Windows.UI.Notifications.ToastNotification($xml);"
        "[Windows.UI.Notifications.ToastNotificationManager]::"
        "CreateToastNotifier($env:TOAST_APPID).Show($toast)"
    )
    env = dict(os.environ, TOAST_XML=xml, TOAST_APPID=_toast_appid())
    exe = FULL_POWERSHELL if os.path.exists(FULL_POWERSHELL) else "powershell"
    if debug:
        r = subprocess.run([exe, "-NoProfile", "-Command", ps], env=env,
                           capture_output=True, text=True)
        sys.stdout.write(r.stdout)
        sys.stderr.write(r.stderr)
        return r.returncode
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        [exe, "-NoProfile", "-Command", ps], env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, creationflags=flags)
    return None


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

    if event == "UserPromptSubmit":
        state = "working"
    elif event == "Notification":
        state = "waiting_permission" if "permission" in msg else "waiting_input"
    else:
        state = "waiting_input"

    if session_id:
        write_state(session_id, state, event, cwd, transcript, title, msg, prompt)

    _console_progress(state)  # WT tab dot (no-op under Cursor / non-WT)

    # toast only for Stop / Notification (UserPromptSubmit is state-only)
    if event == "UserPromptSubmit":
        return
    if event == "Notification":
        head = HEAD_PERMISSION if state == "waiting_permission" else HEAD_WAITING
        body = (title + "\n" + msg) if msg else title
    else:
        head = HEAD_WAITING
        body = title
    show_toast(head, body, state)


def _cli(args):
    """Manual/setup entry points; unlike the hook path, errors DO surface."""
    cmd = args[0]
    if cmd == "--make-icons":
        for p in make_icons(force="--force" in args[1:]):
            print(p)
    elif cmd == "--test-toast":
        state = args[1] if len(args) > 1 else "waiting_input"
        if state not in STATE_COLORS:
            raise SystemExit("state must be one of: " + " / ".join(STATE_COLORS))
        rc = show_toast("TEST " + state, "session_notify --test-toast", state,
                        debug=True)
        print("appid =", _toast_appid())
        print("click =", _click_uri() or "(none)")
        print("powershell exit =", rc)
        raise SystemExit(rc)
    elif cmd == "--test-osc":
        # run INSIDE a WT tab: the tab dot should turn yellow/red (or clear)
        state = args[1] if len(args) > 1 else "waiting_permission"
        if state not in _OSC_STATE:
            raise SystemExit("state must be one of: " + " / ".join(_OSC_STATE))
        ok = _console_progress(state)
        print("state =", state, "-> OSC 9;4 written:", ok)
    else:
        raise SystemExit("unknown option: " + cmd)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        _cli(sys.argv[1:])
        sys.exit(0)
    try:
        main()
    except Exception:  # never break Claude Code because of a hook
        pass
    sys.exit(0)
