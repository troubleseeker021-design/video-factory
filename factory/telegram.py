"""Tiny Telegram Bot API client (your phone = the factory's control room)."""
import json
import os

import requests

API = "https://api.telegram.org"


def _token():
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


def call(method, files=None, http_timeout=180, **params):
    if files is None:  # text-only calls are safe to repeat
        from .common import retry
        return retry(lambda: _call(method, None, http_timeout, **params), tries=4, wait=5, what=f"Telegram {method}")
    return _call(method, files, http_timeout, **params)


def _call(method, files=None, http_timeout=180, **params):
    url = f"{API}/bot{_token()}/{method}"
    for k, v in list(params.items()):
        if isinstance(v, (dict, list)):
            params[k] = json.dumps(v)
    r = requests.post(url, data=params, files=files, timeout=(30, http_timeout))
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {data.get('description')}")
    return data["result"]


def send_message(chat_id, text, buttons=None):
    params = {"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True}
    if buttons:
        params["reply_markup"] = {"inline_keyboard": buttons}
    return call("sendMessage", **params)


def send_video(chat_id, path, caption, buttons=None):
    params = {"chat_id": chat_id, "caption": caption[:1000], "supports_streaming": True}
    if buttons:
        params["reply_markup"] = {"inline_keyboard": buttons}
    with open(path, "rb") as f:
        # Telegram can take several minutes to process a video before it answers
        return call("sendVideo", files={"video": ("video.mp4", f, "video/mp4")}, http_timeout=900, **params)


def send_audio(chat_id, path, caption, title="voice"):
    with open(path, "rb") as f:
        return call("sendAudio", files={"audio": (f"{title}.mp3", f, "audio/mpeg")}, chat_id=chat_id,
                    caption=caption[:1000], title=title)


def get_updates(offset):
    return call("getUpdates", offset=offset, timeout=0, allowed_updates=["message", "callback_query"])


def answer_callback(cb_id, text):
    try:
        call("answerCallbackQuery", callback_query_id=cb_id, text=text[:190])
    except Exception:
        pass


def clear_buttons(chat_id, message_id):
    try:
        call("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id, reply_markup={"inline_keyboard": []})
    except Exception:
        pass


def download(file_id, dest):
    info = call("getFile", file_id=file_id)
    url = f"{API}/file/bot{_token()}/{info['file_path']}"
    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    return dest
