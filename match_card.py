from __future__ import annotations

import os
import urllib.request
from io import BytesIO
from typing import Any, Dict


W, H = 1024, 1280
LEFT_W, BOTTOM_Y = 62, 1018
PANEL = (21, 20, 25, 255)
WHITE = (246, 246, 246, 255)
GREEN = (216, 255, 48, 255)
LINE = (232, 232, 232, 235)

FONT_URL = {
    "medium": "https://raw.githubusercontent.com/google/fonts/main/ofl/sofiasans/SofiaSans%5Bwght%5D.ttf",
    "italic": "https://raw.githubusercontent.com/google/fonts/main/ofl/sofiasans/SofiaSans-Italic%5Bwght%5D.ttf",
}
ROOT_DIR = os.path.dirname(__file__)
FONT_FILES = {
    "extra_italic": os.path.join(ROOT_DIR, "fonts", "SofiaSans-ExtraBoldItalic.ttf"),
}


def _font(kind: str, size: int):
    from PIL import ImageFont

    bundled_path = FONT_FILES.get(kind)
    if bundled_path and os.path.exists(bundled_path):
        try:
            return ImageFont.truetype(bundled_path, size)
        except Exception:
            pass

    name = "SofiaSans[wght].ttf" if kind == "medium" else "SofiaSans-Italic[wght].ttf"
    path = os.path.join("/tmp", name)
    if not os.path.exists(path):
        urllib.request.urlretrieve(FONT_URL["medium" if kind == "medium" else "italic"], path)
    try:
        font = ImageFont.truetype(path, size)
        if hasattr(font, "set_variation_by_axes"):
            try:
                font.set_variation_by_axes([500 if kind == "medium" else 800])
            except Exception:
                pass
        return font
    except Exception:
        return ImageFont.load_default()


def _size(draw: Any, text: str, font: Any) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0], box[3] - box[1]


def _fit(draw: Any, text: str, size: int, width: int):
    font = _font("extra_italic", size)
    while size > 26 and _size(draw, text, font)[0] > width:
        size -= 2
        font = _font("extra_italic", size)
    return font


def _right(draw: Any, x: int, y: int, text: str, font: Any, fill: tuple[int, int, int, int]) -> None:
    tw, _ = _size(draw, text, font)
    draw.text((x - tw, y), text, font=font, fill=fill)


def _has_cyrillic(text: str) -> bool:
    return any("а" <= ch.lower() <= "я" or ch.lower() == "ё" for ch in text)


def _latin_to_ru(text: str) -> str:
    combos = [
        ("sch", "ш"),
        ("shch", "щ"),
        ("eigh", "ей"),
        ("ough", "оу"),
        ("kh", "х"),
        ("ch", "ч"),
        ("sh", "ш"),
        ("zh", "ж"),
        ("ts", "ц"),
        ("ya", "я"),
        ("ja", "я"),
        ("yu", "ю"),
        ("ju", "ю"),
        ("yo", "ё"),
        ("jo", "ё"),
        ("ye", "е"),
        ("ck", "к"),
        ("ph", "ф"),
    ]
    chars = {
        "a": "а",
        "b": "б",
        "c": "к",
        "d": "д",
        "e": "е",
        "f": "ф",
        "g": "г",
        "h": "х",
        "i": "и",
        "j": "дж",
        "k": "к",
        "l": "л",
        "m": "м",
        "n": "н",
        "o": "о",
        "p": "п",
        "q": "к",
        "r": "р",
        "s": "с",
        "t": "т",
        "u": "у",
        "v": "в",
        "w": "в",
        "x": "кс",
        "y": "и",
        "z": "з",
    }
    out: list[str] = []
    src = text.lower()
    i = 0
    while i < len(src):
        ch = src[i]
        if not ("a" <= ch <= "z"):
            out.append(ch)
            i += 1
            continue
        matched = False
        for latin, ru in combos:
            if src.startswith(latin, i):
                out.append(ru)
                i += len(latin)
                matched = True
                break
        if not matched:
            out.append(chars.get(ch, ch))
            i += 1
    return "".join(out)


def _surname(name: str) -> str:
    raw = " ".join(str(name or "TBD").replace(",", " ").split())
    if "/" in raw:
        return " / ".join(_surname(x) for x in raw.split("/"))
    parts = raw.split()
    if len(parts) > 1 and len(parts[-1].replace(".", "")) == 1:
        surname = " ".join(parts[:-1])
    else:
        surname = parts[-1] if parts else "TBD"
    if not _has_cyrillic(surname):
        surname = _latin_to_ru(surname)
    return surname.upper()


def _score(event: Dict[str, Any], side: str) -> Dict[str, Any]:
    raw = event.get("raw") or {}
    obj = raw.get("homeScore" if side == "home" else "awayScore") or {}
    return obj if isinstance(obj, dict) else {}


def _val(score: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if score.get(key) is not None:
            return score.get(key)
    return ""


def _fmt(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _scores(event: Dict[str, Any]) -> tuple[list[str], list[str]]:
    custom_home = event.get("card_home_scores")
    custom_away = event.get("card_away_scores")
    if isinstance(custom_home, list) and isinstance(custom_away, list) and custom_home and custom_away:
        return [str(x) for x in custom_home[:4]], [str(x) for x in custom_away[:4]]

    home, away = _score(event, "home"), _score(event, "away")
    h = [_fmt(_val(home, "current", "display"))]
    a = [_fmt(_val(away, "current", "display"))]
    for idx in range(1, 6):
        hv, av = _val(home, f"period{idx}"), _val(away, f"period{idx}")
        if hv == "" or av == "":
            continue
        h.append(_fmt(hv))
        a.append(_fmt(av))
    return h[:4], a[:4]


def _winner(event: Dict[str, Any]) -> str:
    code = (event.get("raw") or {}).get("winnerCode")
    if str(code) == "1":
        return "home"
    if str(code) == "2":
        return "away"
    try:
        return "home" if float(_scores(event)[0][0]) > float(_scores(event)[1][0]) else "away"
    except Exception:
        return ""


def _stage(event: Dict[str, Any]) -> str:
    raw = event.get("raw") or {}
    for key in ("round", "stage", "flashscore_round"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
    return "МАТЧ ЗАВЕРШЕН"


def _tour_line(event: Dict[str, Any]) -> str:
    custom = str(event.get("card_side_text") or "").strip()
    if custom:
        return custom.upper()
    category = str(event.get("tournament_status") or event.get("category") or "").upper()
    tournament = str(event.get("tournament_name") or "ТУРНИР").upper()
    if "(" in tournament:
        tournament = tournament.split("(", 1)[0].strip()
    return f"{category} {tournament}   {_stage(event)}".strip()


def _left_bar(img: Any, text: str) -> None:
    from PIL import Image, ImageDraw

    bar = Image.new("RGBA", (LEFT_W, H), (0, 0, 0, 0))
    px = bar.load()
    for y in range(H):
        t = y / (H - 1)
        top, mid, bot = (60, 26, 128), (84, 48, 155), (218, 255, 48)
        a, b, k = (top, mid, t / 0.56) if t < 0.56 else (mid, bot, (t - 0.56) / 0.44)
        for x in range(LEFT_W):
            grain = ((x * 17 + y * 23) % 37) - 18
            px[x, y] = tuple(max(0, min(255, int(a[i] + (b[i] - a[i]) * k + grain * 0.6))) for i in range(3)) + (255,)

    tmp = Image.new("RGBA", (H, LEFT_W), (0, 0, 0, 0))
    d = ImageDraw.Draw(tmp)
    d.text((24, 24), text, font=_font("medium", 28), fill=WHITE)
    bar.alpha_composite(tmp.rotate(270, expand=True), (0, 0))
    img.alpha_composite(bar, (0, 0))


def build_match_card_png(event: Dict[str, Any]) -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    _left_bar(img, _tour_line(event))
    draw.rectangle((LEFT_W, BOTTOM_Y, W, H), fill=PANEL)

    name_x, y1, y2 = 118, 1064, 1167
    score_y1, score_y2 = 1060, 1163
    cols = [668, 770, 872, 975]
    draw.line((name_x, 1156, 995, 1156), fill=LINE, width=2)
    for x in (704, 805, 907):
        draw.line((x, 1068, x, 1240), fill=LINE, width=2)

    home_name = _surname(str(event.get("card_home_name") or event.get("home_name") or "TBD"))
    away_name = _surname(str(event.get("card_away_name") or event.get("away_name") or "TBD"))
    winner = _winner(event)
    draw.text((name_x, y1), home_name, font=_fit(draw, home_name, 72, 445), fill=GREEN if winner == "home" else WHITE)
    draw.text((name_x, y2), away_name, font=_fit(draw, away_name, 72, 445), fill=GREEN if winner == "away" else WHITE)

    home_scores, away_scores = _scores(event)
    score_font = _font("extra_italic", 72)
    for idx, value in enumerate(home_scores):
        _right(draw, cols[idx], score_y1, value, score_font, GREEN if idx == 0 and winner == "home" else WHITE)
    for idx, value in enumerate(away_scores):
        _right(draw, cols[idx], score_y2, value, score_font, GREEN if idx == 0 and winner == "away" else WHITE)

    out = BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()
