"""The office diary: each agent writes a short line about what it is doing.
The lines are pushed (about every 20 seconds) to the file live.json on a separate
branch called "live" of your GitHub repository, where the Office page reads them.
Everything here is best-effort: if the diary fails, the factory keeps working."""
import atexit
import base64
import datetime as dt
import json
import os
import threading
import time

from .common import DATA

LOCAL = os.path.join(DATA, "live.json")
BRANCH = "live"
KEEP = 120
_events = []
_lock = threading.Lock()
_dirty = False
_thread = None
_remote_loaded = False
_status = {"state": "idle", "title": ""}


def _api(method, path, **kw):
    import requests
    repo, token = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_TOKEN")
    if not repo or not token:
        return None
    return requests.request(method, f"https://api.github.com/repos/{repo}{path}", timeout=20,
                            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}, **kw)


def _ensure_branch():
    r = _api("GET", f"/git/ref/heads/{BRANCH}")
    if r is not None and r.status_code == 404:
        main = _api("GET", "/git/ref/heads/main").json()
        _api("POST", "/git/refs", json={"ref": f"refs/heads/{BRANCH}", "sha": main["object"]["sha"]})


def _payload():
    return {"updated": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z", "status": _status, "events": _events[-KEEP:]}


def _push():
    global _dirty, _remote_loaded, _events
    try:
        _ensure_branch()
        r = _api("GET", f"/contents/live.json?ref={BRANCH}")
        sha = None
        if r is not None and r.status_code == 200:
            info = r.json()
            sha = info.get("sha")
            if not _remote_loaded:  # keep earlier runs' events so the office has history
                try:
                    old = json.loads(base64.b64decode(info["content"]).decode())
                    seen = {(e["t"], e["agent"], e["text"]) for e in _events}
                    with _lock:
                        _events = [e for e in old.get("events", []) if (e["t"], e["agent"], e["text"]) not in seen] + _events
                except Exception:
                    pass
        _remote_loaded = True
        with _lock:
            body = json.dumps(_payload(), ensure_ascii=False)
            _dirty = False
        data = {"message": "office: live update", "branch": BRANCH,
                "content": base64.b64encode(body.encode()).decode()}
        if sha:
            data["sha"] = sha
        _api("PUT", "/contents/live.json", json=data)
    except Exception:
        pass


def _loop():
    while True:
        time.sleep(20)
        if _dirty:
            _push()


def flush():
    if _dirty and os.environ.get("GITHUB_TOKEN"):
        _push()


def note(agent, text, kind="work", **extra):
    """agent: manager, writer, narrator, composer, researcher, sound, editor, publisher."""
    global _dirty, _thread
    try:
        ev = {"t": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z", "agent": agent, "text": text, "kind": kind}
        ev.update({k: v for k, v in extra.items() if v is not None})
        with _lock:
            _events.append(ev)
            del _events[:-KEEP]
            _dirty = True
        try:
            with open(LOCAL, "w", encoding="utf-8") as f:
                json.dump(_payload(), f, ensure_ascii=False, indent=1)
        except Exception:
            pass
        if os.environ.get("GITHUB_TOKEN") and _thread is None:
            _thread = threading.Thread(target=_loop, daemon=True)
            _thread.start()
            atexit.register(flush)
    except Exception:
        pass


def status(state, title=""):
    global _dirty
    _status.update({"state": state, "title": title})
    _dirty = True
