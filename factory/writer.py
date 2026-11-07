"""Agent 1 - The Writer.
Gets a script for the next video: from Google Gemini (free key) if configured,
otherwise from the built-in script bank in data/script_bank.json."""
import json
import os
import random
import re
import urllib.request
import urllib.error

from .common import DATA, log, read_json, write_json

BANK = os.path.join(DATA, "script_bank.json")
USED = os.path.join(DATA, "used_scripts.json")
# Every video belongs to a numbered series - gives viewers a reason to follow and binge.
SERIES = {"space": "Space Secrets", "ocean": "Ocean Mysteries", "nature": "Nature's Weirdest",
          "history": "Lost History", "body": "Body Facts", "science": "Science Shocks",
          "animals": "Animal Oddities"}
QUESTIONS = ["Did you already know this?", "Which part surprised you most?",
             "Would you want to see this in person?", "What should we cover next?"]
GEMINI_MODELS = ["gemini-2.5-flash", "gemini-flash-latest", "gemini-2.0-flash"]

PROMPT = """You write viral, 100% factually accurate YouTube Shorts / TikTok / Reels scripts for a
curiosity channel. Niche: {niche}
Pick a TRULY little-known, jaw-dropping topic most people have never heard of (not the usual
"octopus has 3 hearts" style facts). It must NOT repeat any of these past topics: {recent}

Structure (proven by viral-shorts research):
- "hook": the first line, max 14 words, spoken in under 3 seconds. Use a curiosity gap,
  a bold claim or a "this shouldn't exist" twist. Never start with "Did you know" or "In this video".
- "body": 5 to 7 short sentences (under 22 words each), third person, serious and
  thought-provoking tone. Escalate: context -> surprising detail -> an open loop
  ("But that's not the strangest part.") -> the payoff. Use "..." for a dramatic pause once or twice.
  Only verified facts; hedge with "scientists think" when not certain. No medical/financial advice.
- "cta": a final line that LOOPS back into the hook so the video can replay seamlessly.
- Total spoken length 85 to 115 words (about 40 to 50 seconds).
- "title": catchy, under 60 characters, no hashtags.
- "caption": 1-2 sentence social caption with 1 emoji.
- "hashtags": 6 relevant hashtags starting with #.
- "keywords": 3 simple stock-footage search terms (e.g. "ocean", "octopus underwater").
- "theme": one of space, ocean, nature, history, body, science, animals.
- "wiki": 2 exact English Wikipedia article titles about the subject (used to find real photos).
- "visuals": one short stock-footage search phrase for EACH spoken line (hook, each body sentence, cta), same order.
- "emojis": one fitting emoji for EACH spoken line, same order.
- "question": one short, fun question to ask viewers in the comments about this topic
  (makes people comment, e.g. "Would you swim near this?"), max 12 words, no hashtags.
{insights}
Reply with JSON only:
{{"title":"","hook":"","body":["",""],"cta":"","caption":"","hashtags":[],"keywords":[],"theme":"","wiki":[],"visuals":[],"emojis":[],"question":""}}"""


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50]


def _gemini(cfg, recent_titles):
    key = cfg.get("gemini_api_key", "").strip()
    if not key:
        return None
    prompt = PROMPT.format(niche=cfg.get("niche", "facts"), recent="; ".join(recent_titles[-40:]) or "none",
                           insights=cfg.get("_insights", ""))
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 1.0, "responseMimeType": "application/json"},
    }).encode()
    for model in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        try:
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=180) as r:
                resp = json.loads(r.read())
            text = resp["candidates"][0]["content"]["parts"][0]["text"]
            text = text.strip().removeprefix("```json").removesuffix("```").strip()
            s = json.loads(text)
            if s.get("hook") and s.get("body"):
                s["source"] = f"gemini:{model}"
                return s
        except Exception as e:  # timeouts, network hiccups, bad replies -> try next model, then the script bank
            log(f"Gemini ({model}) failed: {e}")
    return None


def next_script(cfg):
    used = read_json(USED, [])
    used_ids = {u["id"] for u in used}
    recent_titles = [u["title"] for u in used]

    script = _gemini(cfg, recent_titles)
    if script is None:
        bank = read_json(BANK, [])
        fresh = [s for s in bank if s["id"] not in used_ids]
        best = [s for s in fresh if not s["id"].startswith("classic-")]
        fresh = best or fresh
        if not fresh:
            log("Script bank is used up - starting over. Add a free Gemini key in config.json for unlimited new scripts.")
            fresh = bank
        script = dict(random.choice(fresh))
        script["source"] = "bank"

    script.setdefault("id", _slug(script["title"]))
    if script["id"] in used_ids and script["source"] != "bank":
        script["id"] += f"-{random.randint(100, 999)}"
    theme = script.get("theme") if script.get("theme") in SERIES else "science"
    script["theme"] = theme
    number = 1 + sum(1 for u in used if u.get("theme") == theme)
    script["series"] = SERIES[theme]
    script["series_tag"] = f"{SERIES[theme]} #{number}"
    if not script.get("question"):
        script["question"] = random.choice(QUESTIONS)
    used.append({"id": script["id"], "title": script["title"], "theme": theme})
    write_json(USED, used)
    return script


def spoken_text(script):
    return " ".join([script["hook"], *script["body"], script["cta"]])
