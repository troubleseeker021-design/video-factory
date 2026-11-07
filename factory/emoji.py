"""3D emoji graphics (Google Noto 3D emoji, Apache-2.0) used as animated stickers.
Picks an emoji that matches the words being spoken."""
import os
import re

from PIL import Image, ImageDraw, ImageFont

from .common import ASSETS, FONTS

EMOJI_DIR = os.path.join(ASSETS, "emoji")
_PACK = os.path.join(ASSETS, "emoji_pack.zip")
if os.path.exists(_PACK):
    import zipfile
    with zipfile.ZipFile(_PACK) as z:
        names = z.namelist()
        have = set(os.listdir(EMOJI_DIR)) if os.path.isdir(EMOJI_DIR) else set()
        if not set(names) <= have:
            os.makedirs(EMOJI_DIR, exist_ok=True)
            z.extractall(EMOJI_DIR)

KEYWORDS = {
    # space
    "space": "🚀", "rocket": "🚀", "astronaut": "👨‍🚀", "astronauts": "👨‍🚀", "moon": "🌕", "lunar": "🌕", "sun": "☀️",
    "star": "⭐", "stars": "✨", "galaxy": "🌌", "universe": "🌌", "planet": "🪐", "planets": "🪐", "saturn": "🪐",
    "venus": "🪐", "mars": "🔴", "earth": "🌍", "world": "🌍", "orbit": "🛰️", "satellite": "🛰️", "telescope": "🔭",
    "comet": "☄️", "meteorite": "☄️", "alien": "👽", "gravity": "🌍", "nasa": "🚀", "pluto": "🪐", "spin": "🔄",
    "spins": "🔄", "light": "💡", "radio": "📻",
    # ocean / water
    "ocean": "🌊", "sea": "🌊", "water": "💧", "wave": "🌊", "waves": "🌊", "octopus": "🐙", "octopuses": "🐙",
    "shark": "🦈", "sharks": "🦈", "fish": "🐟", "whale": "🐋", "dolphin": "🐬", "dolphins": "🐬", "shrimp": "🦐",
    "crab": "🦀", "otter": "🦦", "otters": "🦦", "swim": "🏊", "swimming": "🏊", "swims": "🏊", "ice": "🧊",
    "bathtub": "🛁", "float": "🛟", "drink": "🥤", "glass": "🥛", "rain": "🌧️", "snow": "❄️", "kelp": "🌿",
    # nature
    "tree": "🌳", "trees": "🌲", "forest": "🌲", "plant": "🌱", "plants": "🌱", "grass": "🌾", "flower": "🌸",
    "flowers": "🌸", "leaf": "🍃", "bamboo": "🎋", "volcano": "🌋", "mountain": "🏔️", "everest": "🏔️",
    "desert": "🏜️", "lightning": "⚡", "thunder": "🌩️", "storm": "⛈️", "fire": "🔥", "hot": "🔥", "heat": "🔥",
    "cold": "🥶", "frozen": "🧊", "antarctica": "🧊", "weather": "🌦️", "fruit": "🍓", "banana": "🍌",
    "bananas": "🍌", "strawberry": "🍓", "strawberries": "🍓", "grapes": "🍇", "tomato": "🍅", "tomatoes": "🍅",
    "avocado": "🥑", "avocados": "🥑", "berry": "🫐", "berries": "🫐", "seeds": "🌱", "honey": "🍯",
    # animals
    "animal": "🐾", "animals": "🐾", "cow": "🐄", "cows": "🐄", "elephant": "🐘", "elephants": "🐘",
    "bird": "🐦", "birds": "🐦", "hummingbird": "🐦", "hummingbirds": "🐦", "butterfly": "🦋", "butterflies": "🦋",
    "wombat": "🐨", "wombats": "🐨", "dog": "🐕", "cat": "🐈", "lion": "🦁", "snake": "🐍", "bee": "🐝",
    "ant": "🐜", "spider": "🕷️", "dinosaur": "🦖", "dinosaurs": "🦖", "monkey": "🐒", "apes": "🦍", "unicorn": "🦄",
    "unicorns": "🦄", "egg": "🥚", "eggs": "🥚", "poop": "💩", "feet": "🦶", "trunk": "🐘", "mirror": "🪞",
    # body
    "brain": "🧠", "brains": "🧠", "heart": "❤️", "hearts": "🫀", "blood": "🩸", "bone": "🦴", "bones": "🦴",
    "skull": "💀", "dna": "🧬", "cell": "🦠", "cells": "🦠", "stomach": "🫃", "nose": "👃", "eye": "👁️",
    "eyes": "👀", "baby": "👶", "babies": "👶", "body": "💪", "sleep": "😴", "sleeping": "😴", "sleeps": "😴",
    "energy": "⚡", "acid": "🧪", "doctor": "🩺", "taste": "👅", "scream": "😱", "neurons": "🧠",
    # science / history
    "science": "🔬", "scientists": "🧑‍🔬", "scientist": "🧑‍🔬", "researchers": "🧑‍🔬", "study": "📊",
    "chemistry": "🧪", "atom": "⚛️", "iron": "🔩", "metal": "🔩", "copper": "🟠", "explosion": "💥",
    "explode": "💥", "explosions": "💥", "sound": "🔊", "silent": "🔇", "hear": "👂", "vacuum": "🫙",
    "history": "📜", "ancient": "🏛️", "pyramid": "🔺", "pyramids": "🔺", "egypt": "🐫", "cleopatra": "👑",
    "king": "👑", "queen": "👑", "sultan": "👑", "royal": "👑", "castle": "🏰", "war": "⚔️", "ships": "🚢",
    "ship": "🚢", "palace": "🏰", "university": "🎓", "students": "🎓", "school": "🏫", "book": "📖",
    "tower": "🗼", "eiffel": "🗼", "paris": "🗼", "china": "🧱", "wall": "🧱", "city": "🏙️", "cities": "🏙️",
    "money": "💰", "gold": "🥇", "time": "⏰", "minutes": "⏱️", "seconds": "⏱️", "day": "🌅", "days": "⏳",
    "year": "📅", "years": "⏳", "night": "🌙", "summer": "☀️", "winter": "❄️", "million": "🤯",
    "billion": "🤯", "trillion": "🤯", "fastest": "⚡", "fast": "💨", "speed": "💨", "bullet": "💨",
    "punch": "🥊", "biggest": "🏆", "largest": "🏆", "tallest": "📏", "longest": "📏", "weigh": "⚖️",
    "weighs": "⚖️", "tons": "🏋️", "food": "🍽️", "eat": "🍽️", "secret": "🤫", "myth": "❌", "false": "❌",
    "wrong": "❌", "true": "✅", "impossible": "🚫", "never": "🚫", "friend": "🤝", "friends": "🤝",
    "hands": "🤝", "love": "❤️", "stress": "😰", "weird": "🤪", "crazy": "🤯", "wild": "🤯", "insane": "🤯",
    "follow": "👉", "comment": "💬", "share": "📤", "like": "👍", "question": "❓", "why": "❓", "wait": "✋",
}
KEYWORDS.update({
    "spacecraft": "🛰️", "voyager": "🛰️", "probe": "🛰️", "signal": "📡", "antenna": "📡", "antennas": "📡",
    "transmitter": "📡", "telescope": "🔭", "telescopes": "🔭", "computer": "💻", "computing": "💻", "car": "🚗",
    "battery": "🔋", "bulb": "💡", "message": "✉️", "hello": "👋", "launched": "🚀", "farthest": "🔭",
    "uranium": "☢️", "nuclear": "☢️", "reactor": "☢️", "radiation": "☢️", "frog": "🐸", "liver": "🫀",
    "invisible": "👻", "ghost": "👻", "glow": "✨", "glowing": "✨", "glows": "✨", "camera": "📷", "cameras": "📷",
    "photographer": "📷", "volcano": "🌋", "eardrums": "👂", "sailors": "⛵", "island": "🏝️", "weather": "🌦️",
    "trash": "🗑️", "plastic": "🛍️", "bag": "🛍️", "slime": "🫧", "bubble": "🫧", "flash": "📸", "sonar": "📡",
    "submarine": "🚢", "glass": "🔮", "wind": "🌪️", "winds": "🌪️", "raspberries": "🍓", "rum": "🥃",
    "alcohol": "🍸", "toxic": "☠️", "deadly": "☠️", "dead": "☠️", "die": "☠️", "flamingos": "🦩", "flamingo": "🦩",
    "river": "🏞️", "legend": "📜", "legends": "📜", "aliens": "👽", "wow": "😲", "emus": "🐦", "soldiers": "🪖",
    "guns": "🔫", "dance": "💃", "dancing": "💃", "danced": "💃", "musicians": "🎻", "telegraph": "📟",
    "shocks": "⚡", "aurora": "🌌", "auroras": "🌌", "grid": "🔌", "moon": "🌕", "jets": "⛲", "geysers": "⛲",
    "whale": "🐋", "whales": "🐋", "song": "🎵", "sing": "🎵", "sings": "🎵", "surgery": "🩺", "surgeons": "🩺",
    "hippos": "🦛", "crocodiles": "🐊", "lakes": "🏞️", "tunnel": "🚇", "underground": "🕳️", "hole": "🕳️",
    "city": "🏙️", "pressure": "🗜️", "pasta": "🍝", "spaghetti": "🍝", "lasagna": "🍝", "steel": "🔩",
    "hiccups": "😵", "hiccupping": "😵", "hiccupped": "😵", "cancer": "🎗️", "vaccine": "💉", "fossils": "🦴",
    "drill": "⛏️", "drilling": "⛏️", "icebergs": "🧊", "microphones": "🎙️", "monster": "👹", "mirror": "🪞",
    "bacteria": "🦠", "immune": "🛡️", "sand": "⏳", "lightning": "⚡", "tubes": "🧪", "molten": "🔥",
})
GENERIC = ["🤯", "👀", "😮", "💡", "🔥", "✨"]
THEME_POOL = {
    "space": ["🌌", "🛰️", "🚀", "🪐", "🔭", "☄️", "🌠"], "ocean": ["🌊", "🐋", "🐠", "🫧", "🐙", "🦈"],
    "nature": ["🌍", "🌋", "🌿", "⚡", "🏔️", "🌊"], "animals": ["🐾", "🦁", "🐘", "🦋", "🐸", "🐦"],
    "history": ["📜", "🏛️", "⏳", "🗺️", "⚔️", "👑"], "body": ["🧠", "🧬", "🫀", "🦴", "👁️", "🩸"],
    "science": ["🔬", "🧪", "⚛️", "🧬", "💡", "🔭"],
}
REACTIONS = ["🤯", "😱", "👀", "😮"]


def filename(e):
    cps = [f"{ord(c):04x}" for c in e if ord(c) != 0xFE0F]
    return f"emoji_u{'_'.join(cps)}.png"


def pick(text, used=()):
    words = re.findall(r"[a-z]+", text.lower())
    for w in words:
        e = KEYWORDS.get(w)
        if e and e not in used:
            return e
    for w in words:
        if w in KEYWORDS:
            return KEYWORDS[w]
    return None


_cache = {}
_font = None


def image(e, size):
    """Return an RGBA image of emoji `e` at `size` px (3D PNG if bundled, else Noto font)."""
    key = (e, size)
    if key in _cache:
        return _cache[key]
    path = os.path.join(EMOJI_DIR, filename(e))
    img = None
    if os.path.exists(path):
        img = Image.open(path).convert("RGBA")
    else:
        global _font
        try:
            if _font is None:
                _font = ImageFont.truetype(os.path.join(FONTS, "NotoColorEmoji.ttf"), 109)
            img = Image.new("RGBA", (160, 160), (0, 0, 0, 0))
            ImageDraw.Draw(img).text((80, 80), e, font=_font, embedded_color=True, anchor="mm")
            bb = img.getbbox()
            img = img.crop(bb) if bb else None
        except Exception:
            img = None
    if img is None:
        return None
    img.thumbnail((size, size), Image.LANCZOS)
    if len(_cache) > 80:
        _cache.clear()
    _cache[key] = img
    return img
