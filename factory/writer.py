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
          "animals": "Animal Oddities", "places": "Unreal Places", "tech": "How It Works"}
QUESTIONS = ["Did you already know this?", "Which part surprised you most?",
             "Would you want to see this in person?", "What should we cover next?"]
CATCHPHRASE = "And that's the part nobody talks about."
GEMINI_MODELS = ["gemini-flash-latest", "gemini-2.5-flash", "gemini-flash-lite-latest"]
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
                 "The \"cta\" is exactly \"So, which one of these would you visit?\" (no names, no numbers) and "
                 "\"question\" is the same question. Name places simply (\"Lonar Lake in India\"), never "
                 "hard-to-pronounce regional or state names. \"labels\" must hold the place name and "
                 "country (e.g. \"LAKE HILLIER, AUSTRALIA\") on the line where each place is introduced.")

PROMPT = """You write viral, 100% factually accurate YouTube Shorts / Reels scripts for a curiosity
channel. Niche: {niche}
Pick a TRULY little-known, jaw-dropping topic most people have never heard of (not the usual
"octopus has 3 hearts" style facts). It must NOT repeat any of these past topics: {recent}
{format}
{theme_rule}
Never reuse or closely copy any of these recent hooks: {hooks}

Structure (proven by this week's viral shorts):
STYLE = viral 3D-explainer reels: explain HOW something works, step by step, like a mini documentary.
- "hook": the first line, max 14 words. Either a contradiction ("This plane attacks its target by
  flying circles around it.") or a "what if" aimed at the viewer ("If you were frozen for a hundred
  years, you wouldn't wake up."). Never start with "Did you know" or "In this video".
- "body": 6 to 9 short sentences (under 18 words each) forming a cause-and-effect chain: "They did X.
  Then Y. So Z. And when..., ...". Simple everyday metaphors ("your body is made of tiny bags of water").
  Concrete numbers. A twist near the end ("But a little frog in Alaska freezes like this every winter...").
  Write it the way a person TALKS when explaining to a friend, not like an article being read out:
  speak to "you", use spoken connectors ("Now here's the thing.", "So what happens?", "And this is the
  crazy part.", "Think of it like..."), mix very short punchy sentences with medium ones, use contractions
  (it's, they're, you'd). No exclamation marks, no "..." pauses, no questions asking viewers to comment.
  Only verified facts; hedge with "scientists think" when not certain. No medical/financial advice.
- "cta": one short, satisfying final line that wraps up the story (a last surprising detail or a punchy
  conclusion). No "follow", no "comment" - that part is added automatically after it.
- Total spoken length 100 to 150 words (about 35 to 50 seconds).
- "title": catchy, under 60 characters, no hashtags.
- "caption": 1-2 sentence social caption with 1 emoji.
- "hashtags": 6 relevant hashtags starting with #.
- "keywords": 3 simple stock-footage search terms (e.g. "ocean", "octopus underwater").
- "theme": one of space, ocean, nature, history, body, science, animals, places, tech
  ("tech" = HOW a machine / aircraft / vehicle / weapon / engine / building really works inside).
- "wiki": 2 or 3 exact English Wikipedia article titles about the subject(s) (used to find real photos).
- "visuals": one short stock-footage search phrase for EACH spoken line (hook, each body sentence, cta), same order.
- "emojis": one fitting emoji for EACH spoken line, same order.
- "art": for EACH spoken line, a vivid description of ONE 3D-animation shot that shows exactly what the
  line says (subject, action, camera angle, setting; e.g. "tweezers pulling an antenna off a moth and
  placing it on a green circuit board, macro close-up"). No text in the picture. Same order.
- "facts": for EACH spoken line, a tiny on-screen fact box with a concrete number or spec, max 5 words
  (e.g. "Core: about 1,277 °C", "Turret turns 110° each way", "Top speed: 3,500 km/h"), or "" when the
  line has no hard fact. Use it on about half of the lines. Same order.
- "labels": for EACH spoken line, a short on-screen label in CAPS (a place, person or thing name, max 4 words)
  or "" when the line needs none. Same order.
- "thumb": 2 to 4 shocking words for the thumbnail in CAPS that make people stop scrolling,
  e.g. "IT BREATHES OXYGEN?!" or "NOBODY SURVIVED THIS". Never just repeat the title.
- "question": one interesting question shown at the end of the video so viewers comment. It must be
  about THIS topic and easy to answer in a word or two - a choice, a dare, a guess or an opinion
  (e.g. "Would you swim in this lake? Yes or no?", "Guess how old this tree is?",
  "Which would you rather visit, A or B?"). Max 12 words, no hashtags, no emojis.
- "confidence": "high" only if EVERY fact is well documented; otherwise "medium" or "low".
- "doubts": the facts you are not fully sure about (empty list if none).
{viewer}{insights}
Reply with JSON only:
{{"title":"","hook":"","body":["",""],"cta":"","caption":"","hashtags":[],"keywords":[],"theme":"","wiki":[],"visuals":[],"emojis":[],"art":[],"facts":[],"labels":[],"thumb":"","question":"","confidence":"high","doubts":[]}}"""


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
    import time
    for attempt in range(2):
        for model in _models(key):
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
            except Exception as e:  # timeouts, busy (503), retired model (404) -> try the next one
                log(f"Gemini ({model}) failed: {e}")
        time.sleep(20)  # Google busy? wait a moment and go round once more
    return None


_MODELS = None


def _models(key):
    """Google retires model names over time, so ask which "flash" models this key can use right now."""
    global _MODELS
    if _MODELS is None:
        found = []
        try:
            with urllib.request.urlopen(f"https://generativelanguage.googleapis.com/v1beta/models?key={key}&pageSize=200",
                                        timeout=30) as r:
                for m in json.loads(r.read()).get("models", []):
                    name = m.get("name", "").split("/")[-1]
                    if ("flash" in name and "generateContent" in m.get("supportedGenerationMethods", [])
                            and not any(x in name for x in ("image", "tts", "audio", "live", "thinking", "exp"))):
                        found.append(name)
        except Exception as e:
            log(f"Gemini model list failed: {e}")
        # newest first: stable "-latest" aliases, then highest version numbers, "lite" last
        found.sort(key=lambda n: ("lite" in n, "latest" not in n, "preview" in n, [-int(x) for x in re.findall(r"\d+", n)]))
        _MODELS = list(dict.fromkeys([*found[:4], *GEMINI_MODELS]))
        log(f"Gemini models to try: {', '.join(_MODELS)}")
    return _MODELS


def _norm_hook(h):
    return " ".join(re.findall(r"[a-z0-9']+", (h or "").lower())[:6])


def _plan(used):
    """Decide series/format for the next video from what was made before."""
    last_theme = used[-1].get("theme") if used else None
    last_format = used[-1].get("format") if used else None
    since_places = next((i for i, u in enumerate(reversed(used)) if u.get("theme") == "places"), len(used))
    want_places = since_places >= 2 and last_theme != "places"
    since_tech = next((i for i, u in enumerate(reversed(used)) if u.get("theme") == "tech"), len(used))
    _plan.want_tech = (not want_places) and since_tech >= 2 and last_theme != "tech"
    fmt = "places" if want_places else random.choice([f for f in FORMATS if f != last_format])
    return want_places, fmt, last_theme


def _gemini(cfg, used, want_places, fmt, last_theme):
    recent_titles = [u["title"] for u in used][-40:]
    cutoff = (dt.date.today() - dt.timedelta(days=30)).isoformat()
    hooks = [u["hook"] for u in used if u.get("hook") and u.get("date", "") >= cutoff][-60:]
    if want_places:
        theme_rule = 'The "theme" MUST be "places".'
    elif getattr(_plan, "want_tech", False):
        theme_rule = ('The "theme" MUST be "tech": explain how one impressive machine, aircraft, vehicle, weapon '
                      'system, engine or structure works inside, step by step (series "How It Works").')
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
        if not want_places and not getattr(_plan, "want_tech", False) and s.get("theme") in (last_theme, "places") and attempt == 0:
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
    if theme != "places" and random.random() < cfg.get("catchphrase_chance", 0) and CATCHPHRASE not in script["body"]:
        n = len(script["body"]) + 1  # per-line lists are [hook, *body, cta]; insert before the cta
        script["body"] = [*script["body"], CATCHPHRASE]
        for k, filler in (("emojis", "🤫"), ("visuals", ""), ("labels", ""), ("art", ""), ("facts", "")):
            if script.get(k):
                script[k] = [*script[k][:n], filler, *script[k][n:]]
    # "Which one: 1, 2 or 3?" sounds unclear - ask it as a plain question (no names, no numbers)
    if re.search(r"1, 2,? or 3", script.get("cta", "")):
        script["cta"] = "So, which one of these would you visit?"
        script["question"] = "Which one of these would you visit?"
    # the English voice stumbles over Indian state names - keep the pace by just saying "India"
    for k in ("hook", "cta"):
        script[k] = _easy_names(script.get(k, ""))
    script["body"] = [_easy_names(b) for b in script.get("body", [])]
    if cfg.get("spoken_outro", True):  # the narrator asks the question and asks for a like + follow
        if script.get("cta", "").strip().endswith("?"):  # the last line already IS the question
            script["question"] = script["question"] if not re.search(r"1, 2,? or 3", script["question"]) \
                else script["cta"]
            script["q_in_cta"] = True
            script["outro"] = "Comment below, and follow for more."
        else:
            q = script["question"].strip().rstrip(".")
            q = q if q.endswith("?") else q + "?"
            script["outro"] = f"{q} Comment below, and follow for more."
    conf = str(script.get("confidence", "high")).lower()
    script["needs_check"] = conf != "high" or bool(script.get("doubts"))
    used.append({"id": script["id"], "title": script["title"], "theme": theme, "format": script["format"],
                 "hook": script.get("hook", ""), "date": dt.date.today().isoformat()})
    write_json(USED, used)
    return script


HARD_NAMES = ("Andhra Pradesh|Arunachal Pradesh|Assam|Bihar|Chhattisgarh|Goa|Gujarat|Haryana|Himachal Pradesh|"
              "Jharkhand|Karnataka|Kerala|Madhya Pradesh|Maharashtra|Manipur|Meghalaya|Mizoram|Nagaland|Odisha|"
              "Punjab|Rajasthan|Sikkim|Tamil Nadu|Telangana|Tripura|Uttar Pradesh|Uttarakhand|West Bengal|"
              "Ladakh|Jammu and Kashmir|Andaman and Nicobar Islands")


def _easy_names(text):
    """'Lonar Lake in Maharashtra' -> 'Lonar Lake in India' (only for the English voice; labels keep the state)."""
    t = re.sub(rf"\b(in|from|of) ({HARD_NAMES})\b(, India)?", r"\1 India", text)
    return re.sub(r"\bin India,? India\b", "in India", t)


def spoken_text(script):
    return " ".join([script["hook"], *script["body"], script["cta"], *([script["outro"]] if script.get("outro") else [])])
