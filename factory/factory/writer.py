"""Agent 1 - The Writer.
Gets a script for the next video: from Google Gemini (free key) if configured,
otherwise from the built-in script bank in data/script_bank.json.

Originality rules (so the channel never feels mass-produced):
  - every video belongs to a numbered series; never two videos in a row from the same series
  - one "Unreal Places" episode about every third video (hand-written Ep 1-10 first)
  - formats rotate: standard fact story, "Real or fake?" quiz, "Myth vs truth", mini countdown
  - a recurring catchphrase closes most videos
  - a hook is never reused within 30 days
  - Gemini rates its own confidence; anything not "high" waits for the owner's approval"""
import datetime as dt
import json
import os
import random
import re
import urllib.request

from .common import DATA, log, read_json, write_json

BANK = os.path.join(DATA, "script_bank.json")
USED = os.path.join(DATA, "used_scripts.json")
QUESTIONS_FILE = os.path.join(DATA, "viewer_questions.json")
# Every video belongs to a numbered series - gives viewers a reason to follow and binge.
SERIES = {"space": "Space Secrets", "ocean": "Ocean Mysteries", "nature": "Nature's Weirdest",
          "history": "Lost History", "body": "Body Facts", "science": "Science Shocks",
          "animals": "Animal Oddities", "places": "Unreal Places"}
QUESTIONS = ["Did you already know this?", "Which part surprised you most?",
             "Would you want to see this in person?", "What should we cover next?"]
CATCHPHRASE = "And that's the part nobody talks about."
GEMINI_MODELS = ["gemini-2.5-flash", "gemini-flash-latest", "gemini-2.0-flash"]
FORMATS = {
    "standard": "FORMAT: a standard fact story about ONE jaw-dropping subject.",
    "quiz": ('FORMAT: "Real or fake?" quiz. The hook asks viewers to guess if something is real '
             '(e.g. "Real or fake? This lake is bright pink."), the body builds suspense, then reveals it is REAL '
             "and explains why. Everything must be a real, verified fact."),
    "myth": ('FORMAT: "Myth vs truth". The hook states a popular belief most people think is true, '
             "the body shows why it is wrong and what is actually true, with the evidence."),
    "countdown": ("FORMAT: mini countdown of exactly 3 related items (\"Number three... number two... "
                  "and number one...\"), saving the most shocking for last so people stay to the end."),
}
PLACES_FORMAT = ("FORMAT: \"Unreal Places\": exactly 3 real places on Earth that look fake, one surprising fact "
                 "each, the most shocking place last. Say \"Number one\", \"Number two\", \"Number three\". "
                 "End with \"Which one would you visit: 1, 2 or 3?\". \"labels\" must hold the place name and "
                 "country (e.g. \"LAKE HILLIER, AUSTRALIA\") on the line where each place is introduced.")

PROMPT = """You write viral, 100% factually accurate YouTube Shorts / Reels scripts for a curiosity
channel. Niche: {niche}
Pick a TRULY little-known, jaw-dropping topic most people have never heard of (not the usual
"octopus has 3 hearts" style facts). It must NOT repeat any of these past topics: {recent}
{format}
{theme_rule}
Never reuse or closely copy any of these recent hooks: {hooks}

Structure (proven by this week's viral shorts):
- "hook": the first line, max 14 words, spoken in under 3 seconds. Make it a CLAIM viewers want to
  verify ("This looks fake, but it's 100% real."), a curiosity gap or a "this shouldn't exist" twist.
  Never start with "Did you know" or "In this video".
- "body": 5 to 7 short sentences (under 20 words each), third person, upbeat and energetic but
  intriguing (NOT slow or gloomy). Escalate: context -> surprising detail -> an open loop
  ("But that's not the strangest part.") -> the payoff. Use "..." for a dramatic pause once.
  Only verified facts; hedge with "scientists think" when not certain. No medical/financial advice.
- "cta": a final line that LOOPS back into the hook so the video can replay seamlessly.
- Total spoken length 80 to 110 words (about 35 to 45 seconds).
- "title": catchy, under 60 characters, no hashtags.
- "caption": 1-2 sentence social caption with 1 emoji.
- "hashtags": 6 relevant hashtags starting with #.
- "keywords": 3 simple stock-footage search terms (e.g. "ocean", "octopus underwater").
- "theme": one of space, ocean, nature, history, body, science, animals, places.
- "wiki": 2 or 3 exact English Wikipedia article titles about the subject(s) (used to find real photos).
- "visuals": one short stock-footage search phrase for EACH spoken line (hook, each body sentence, cta), same order.
- "emojis": one fitting emoji for EACH spoken line, same order.
- "labels": for EACH spoken line, a short on-screen label in CAPS (a place, person or thing name, max 4 words)
  or "" when the line needs none. Same order.
- "thumb": 2 to 4 shocking words for the thumbnail in CAPS that make people stop scrolling,
  e.g. "IT BREATHES OXYGEN?!" or "NOBODY SURVIVED THIS". Never just repeat the title.
- "question": one short, fun question to ask viewers in the comments about this topic
  (makes people comment, e.g. "Would you swim near this?"), max 12 words, no hashtags.
- "confidence": "high" only if EVERY fact is well documented; otherwise "medium" or "low".
- "doubts": the facts you are not fully sure about (empty list if none).
{viewer}{insights}
Reply with JSON only:
{{"title":"","hook":"","body":["",""],"cta":"","caption":"","hashtags":[],"keywords":[],"theme":"","wiki":[],"visuals":[],"emojis":[],"labels":[],"thumb":"","question":"","confidence":"high","doubts":[]}}"""


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50]


def gemini_json(cfg, prompt, temperature=1.0):
    """Ask Gemini for a JSON answer (tries several free models). Returns dict or None."""
    key = cfg.get("gemini_api_key", "").strip()
    if not key:
        return None
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "responseMimeType": "application/json"},
    }).encode()
    for model in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        try:
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=180) as r:
                resp = json.loads(r.read())
            text = resp["candidates"][0]["content"]["parts"][0]["text"]
            text = text.strip().removeprefix("```json").removesuffix("```").strip()
            out = json.loads(text)
            out["_model"] = model
            return out
        except Exception as e:  # timeouts, network hiccups, bad replies -> try next model
            log(f"Gemini ({model}) failed: {e}")
    return None


def _norm_hook(h):
    return " ".join(re.findall(r"[a-z0-9']+", (h or "").lower())[:6])


def _plan(used):
    """Decide series/format for the next video from what was made before."""
    last_theme = used[-1].get("theme") if used else None
    last_format = used[-1].get("format") if used else None
    since_places = next((i for i, u in enumerate(reversed(used)) if u.get("theme") == "places"), len(used))
    want_places = since_places >= 2 and last_theme != "places"
    fmt = "places" if want_places else random.choice([f for f in FORMATS if f != last_format])
    return want_places, fmt, last_theme


def _gemini(cfg, used, want_places, fmt, last_theme):
    recent_titles = [u["title"] for u in used][-40:]
    cutoff = (dt.date.today() - dt.timedelta(days=30)).isoformat()
    hooks = [u["hook"] for u in used if u.get("hook") and u.get("date", "") >= cutoff][-60:]
    if want_places:
        theme_rule = 'The "theme" MUST be "places".'
    else:
        theme_rule = (f'The "theme" must NOT be "{last_theme}" (we never post the same series twice in a row) '
                      'and must not be "places".' if last_theme else 'The "theme" must not be "places".')
    viewer = ""
    vq = read_json(QUESTIONS_FILE, [])
    if vq and random.random() < 0.35 and not want_places:
        q = random.choice(vq[-15:])
        viewer = f'\nA viewer asked: "{q["text"][:160]}". If it makes a great, verifiable video, answer it.\n'
    prompt = PROMPT.format(niche=cfg.get("niche", "facts"), recent="; ".join(recent_titles) or "none",
                           format=PLACES_FORMAT if want_places else FORMATS[fmt], theme_rule=theme_rule,
                           hooks="; ".join(hooks) or "none", viewer=viewer, insights=cfg.get("_insights", ""))
    recent_hooks = {_norm_hook(h) for h in hooks}
    for attempt in range(2):
        s = gemini_json(cfg, prompt)
        if not s or not s.get("hook") or not s.get("body"):
            continue
        if _norm_hook(s["hook"]) in recent_hooks and attempt == 0:
            log("  hook was too similar to a recent one - asking again")
            continue
        if not want_places and s.get("theme") in (last_theme, "places") and attempt == 0:
            log("  same series as last video - asking again")
            continue
        s["source"] = f"gemini:{s.pop('_model', '')}"
        return s
    return None


def next_script(cfg):
    used = read_json(USED, [])
    used_ids = {u["id"] for u in used}
    want_places, fmt, last_theme = _plan(used)
    bank = read_json(BANK, [])

    script = None
    if want_places:  # hand-written Unreal Places episodes go first, in order
        queued = [s for s in bank if s.get("theme") == "places" and s["id"] not in used_ids]
        if queued:
            script = dict(sorted(queued, key=lambda s: s.get("episode", 99))[0])
            script["source"] = "bank:unreal-places"
    if script is None:
        script = _gemini(cfg, used, want_places, fmt, last_theme)
    if script is None:
        fresh = [s for s in bank if s["id"] not in used_ids and s.get("theme") != last_theme
                 and (s.get("theme") == "places") == want_places]
        fresh = fresh or [s for s in bank if s["id"] not in used_ids] or bank
        if not [s for s in bank if s["id"] not in used_ids]:
            log("Script bank is used up - starting over. Add a free Gemini key for unlimited new scripts.")
        script = dict(random.choice(fresh))
        script["source"] = "bank"

    script.setdefault("id", _slug(script["title"]))
    if script["id"] in used_ids and not script["source"].startswith("bank"):
        script["id"] += f"-{random.randint(100, 999)}"
    theme = script.get("theme") if script.get("theme") in SERIES else "science"
    script["theme"] = theme
    script["format"] = "places" if theme == "places" else fmt
    number = 1 + sum(1 for u in used if u.get("theme") == theme)
    script["series"] = SERIES[theme]
    script["series_tag"] = f"{SERIES[theme]} Ep {number}" if theme == "places" else f"{SERIES[theme]} #{number}"
    if not script.get("question"):
        script["question"] = random.choice(QUESTIONS)
    # recurring catchphrase on most videos (before the looping last line)
    if theme != "places" and random.random() < 0.7 and CATCHPHRASE not in script["body"]:
        n = len(script["body"]) + 1  # per-line lists are [hook, *body, cta]; insert before the cta
        script["body"] = [*script["body"], CATCHPHRASE]
        for k, filler in (("emojis", "🤫"), ("visuals", ""), ("labels", "")):
            if script.get(k):
                script[k] = [*script[k][:n], filler, *script[k][n:]]
    conf = str(script.get("confidence", "high")).lower()
    script["needs_check"] = conf != "high" or bool(script.get("doubts"))
    used.append({"id": script["id"], "title": script["title"], "theme": theme, "format": script["format"],
                 "hook": script.get("hook", ""), "date": dt.date.today().isoformat()})
    write_json(USED, used)
    return script


def spoken_text(script):
    return " ".join([script["hook"], *script["body"], script["cta"]])
