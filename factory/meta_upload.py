"""Agent 9 - The Publisher (Instagram Reels + Facebook Page Reels) via the official Meta Graph API.
Needs: META_PAGE_ID, META_PAGE_TOKEN (long-lived Page token) and IG_USER_ID
(see CLOUD SETUP GUIDE.txt, part 5 - META_SETUP.bat prints all three for you)."""
import json
import os
import time

import requests

from .common import log

VER = os.environ.get("META_GRAPH_VERSION", "v24.0")
G = f"https://graph.facebook.com/{VER}"


def _check(r):
    try:
        data = r.json()
    except ValueError:
        r.raise_for_status()
        return {}
    if r.status_code >= 400 or "error" in data:
        raise RuntimeError(f"Meta API error: {data.get('error', data)}")
    return data


TRIAL = False  # Instagram "trial reels" - our Meta app has no permission for them yet (error code 10)


def _create(ig_user_id, data):
    """Create the Reel container - as a trial reel when allowed, otherwise a normal reel."""
    global TRIAL
    if TRIAL:
        try:
            trial = dict(data, trial_params=json.dumps({"graduation_strategy": "SS_PERFORMANCE"}))
            trial.pop("share_to_feed", None)
            return _check(requests.post(f"{G}/{ig_user_id}/media", data=trial, timeout=60))
        except Exception as e:
            log(f"  trial reel not accepted ({str(e)[:120]}) - posting as a normal reel")
            TRIAL = False
    return _check(requests.post(f"{G}/{ig_user_id}/media", data=data, timeout=60))


def _instagram_once(video_path, caption, ig_user_id, token):
    size = os.path.getsize(video_path)
    c = _create(ig_user_id, {
        "media_type": "REELS", "upload_type": "resumable", "caption": caption[:2200], "thumb_offset": "0",
        "share_to_feed": "true", "access_token": token})
    cid = c["id"]
    with open(video_path, "rb") as f:
        _check(requests.post(f"https://rupload.facebook.com/ig-api-upload/{VER}/{cid}", data=f, timeout=600, headers={
            "Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size)}))
    for _ in range(60):  # wait for Instagram to process (up to ~5 min)
        st = _check(requests.get(f"{G}/{cid}", params={"fields": "status_code,status", "access_token": token}, timeout=30))
        if st.get("status_code") == "FINISHED":
            break
        if st.get("status_code") == "ERROR":
            raise RuntimeError(f"Instagram processing failed: {st.get('status')}")
        time.sleep(5)
    pub = _check(requests.post(f"{G}/{ig_user_id}/media_publish", data={"creation_id": cid, "access_token": token}, timeout=60))
    link = _check(requests.get(f"{G}/{pub['id']}", params={"fields": "permalink", "access_token": token}, timeout=30))
    url = link.get("permalink", f"instagram media {pub['id']}")
    log(f"  posted to Instagram: {url}")
    return url


def facebook_reel(video_path, description, page_id, token, cover=None):
    size = os.path.getsize(video_path)
    start = _check(requests.post(f"{G}/{page_id}/video_reels", data={"upload_phase": "start", "access_token": token}, timeout=60))
    vid = start["video_id"]
    with open(video_path, "rb") as f:
        _check(requests.post(f"https://rupload.facebook.com/video-upload/{VER}/{vid}", data=f, timeout=600, headers={
            "Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size)}))
    _check(requests.post(f"{G}/{page_id}/video_reels", data={
        "upload_phase": "finish", "video_id": vid, "video_state": "PUBLISHED",
        "description": description[:2200], "access_token": token}, timeout=60))
    if cover and os.path.exists(cover):
        try:  # custom cover image for the reel
            with open(cover, "rb") as f:
                _check(requests.post(f"{G}/{vid}/thumbnails", data={"is_preferred": "true", "access_token": token},
                                     files={"source": ("cover.jpg", f, "image/jpeg")}, timeout=60))
        except Exception as e:
            log(f"  (Facebook cover skipped: {str(e)[:120]})")
    url = f"https://www.facebook.com/reel/{vid}"
    log(f"  posted to Facebook: {url}")
    return url


def _wait_and_publish(cid, ig_user_id, token):
    for _ in range(60):
        st = _check(requests.get(f"{G}/{cid}", params={"fields": "status_code,status", "access_token": token}, timeout=30))
        if st.get("status_code") == "FINISHED":
            break
        if st.get("status_code") == "ERROR":
            raise RuntimeError(f"Instagram processing failed: {st.get('status')}")
        time.sleep(5)
    pub = _check(requests.post(f"{G}/{ig_user_id}/media_publish", data={"creation_id": cid, "access_token": token}, timeout=60))
    link = _check(requests.get(f"{G}/{pub['id']}", params={"fields": "permalink", "access_token": token}, timeout=30))
    url = link.get("permalink", f"instagram media {pub['id']}")
    log(f"  posted to Instagram: {url}")
    return url


def instagram_reencoded(video_url, caption, ig_user_id, token):
    """Retry path: download the video, re-encode it to Instagram's safest settings (H.264 Main, 30 fps
    constant, 5 Mbps, AAC 44.1 kHz) and upload the file directly as a normal reel (no trial)."""
    import subprocess
    import tempfile
    from .common import ffmpeg_exe
    global TRIAL
    d = tempfile.mkdtemp(prefix="igfix_")
    src, out = os.path.join(d, "in.mp4"), os.path.join(d, "out.mp4")
    with requests.get(video_url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(src, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", "-i", src,
                    "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,fps=30",
                    "-c:v", "libx264", "-profile:v", "main", "-level", "4.0", "-pix_fmt", "yuv420p",
                    "-b:v", "5M", "-maxrate", "5M", "-bufsize", "10M", "-g", "60", "-r", "30",
                    "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2", "-movflags", "+faststart", out],
                   check=True, timeout=600)
    trial, TRIAL = TRIAL, False
    try:
        return _instagram_once(_fit_for_instagram(out), caption, ig_user_id, token)
    finally:
        TRIAL = trial


def facebook_video_source(fb_url, token):
    """The public MP4 link of a video already posted to the Facebook Page (Meta's own CDN)."""
    vid = fb_url.rstrip("/").split("/")[-1]
    for _ in range(36):  # Facebook needs a little time to process the reel
        d = requests.get(f"{G}/{vid}", params={"fields": "source", "access_token": token}, timeout=30).json()
        if d.get("source"):
            return d["source"]
        time.sleep(5)
    raise RuntimeError("Facebook video link not ready")


def _instagram_from_url(video_url, caption, ig_user_id, token):
    c = _create(ig_user_id, {
        "media_type": "REELS", "video_url": video_url, "caption": caption[:2200], "thumb_offset": "0",
        "share_to_feed": "true", "access_token": token})
    return _wait_and_publish(c["id"], ig_user_id, token)


IG_MAX_S = 50.0  # our reels longer than ~55 s keep failing on Instagram (ProcessingFailedError / 2207076)


def _duration(path):
    import re
    import subprocess
    from .common import ffmpeg_exe
    p = subprocess.run([ffmpeg_exe(), "-i", path], capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", p.stderr)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0


def _fit_for_instagram(path):
    """Instagram copy only: if the video is long, play it a little faster (max 1.3x) so it fits
    ~50 s, re-encoded with Instagram's safest settings. YouTube/Facebook keep the original."""
    import subprocess
    from .common import ffmpeg_exe
    dur = _duration(path)
    if not dur or dur <= IG_MAX_S + 2:
        return path
    f = min(1.3, dur / IG_MAX_S)
    out = os.path.splitext(path)[0] + "_ig.mp4"
    subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", "-i", path,
                    "-vf", f"setpts=PTS/{f:.4f},fps=30", "-af", f"atempo={f:.4f}",
                    "-c:v", "libx264", "-profile:v", "main", "-level", "4.0", "-pix_fmt", "yuv420p",
                    "-b:v", "4M", "-maxrate", "4M", "-bufsize", "8M", "-g", "60",
                    "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2", "-movflags", "+faststart", out],
                   check=True, timeout=600)
    log(f"  Instagram copy: {dur:.0f}s -> {dur / f:.0f}s (played {f:.2f}x faster)")
    return out


def instagram_reel(video_path, caption, ig_user_id, token, tries=2, fallback_url=None):
    """Upload a Reel. Instagram's direct upload sometimes fails with "ProcessingFailedError";
    then we hand Instagram the same video from Facebook's servers instead (fallback_url)."""
    last = None
    try:
        video_path = _fit_for_instagram(video_path)
    except Exception as e:
        log(f"  (could not shorten the Instagram copy: {str(e)[:120]})")
    for i in range(tries):
        try:
            return _instagram_once(video_path, caption, ig_user_id, token)
        except Exception as e:
            last = e
            log(f"  Instagram direct upload {i + 1}/{tries} failed: {str(e)[:160]}")
            time.sleep(15)
    if fallback_url:
        try:
            url = fallback_url() if callable(fallback_url) else fallback_url
            log("  trying Instagram via the Facebook copy of the video")
            return _instagram_from_url(url, caption, ig_user_id, token)
        except Exception as e:
            log(f"  Instagram via Facebook copy failed: {str(e)[:160]}")
            last = e
    raise last
