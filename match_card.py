from __future__ import annotations

import os
import re
import html
import base64
import json
import urllib.request
from io import BytesIO
from typing import Any, Dict


W, H = 1080, 1350
LEFT_W, BOTTOM_Y = 65, 1074
PANEL = (21, 20, 25, 255)
WHITE = (246, 246, 246, 255)
GREEN = (226, 252, 60, 255)
LINE = (232, 232, 232, 235)

FONT_URL = {
    "medium": "https://raw.githubusercontent.com/google/fonts/main/ofl/sofiasans/SofiaSans%5Bwght%5D.ttf",
    "italic": "https://raw.githubusercontent.com/google/fonts/main/ofl/sofiasans/SofiaSans-Italic%5Bwght%5D.ttf",
}
ROOT_DIR = os.path.dirname(__file__)
TEMPLATE_DIR = os.path.join(ROOT_DIR, "assets", "card_templates")
CARD_TEMPLATES = {
    3: os.path.join(TEMPLATE_DIR, "result_2_sets.png"),
    4: os.path.join(TEMPLATE_DIR, "result_3_sets.png"),
}
FONT_FILES = {
    "extra_italic": os.path.join(ROOT_DIR, "fonts", "SofiaSans-ExtraBoldItalic.ttf"),
}
FLASHSCORE_BASE = (os.getenv("FLASHSCORE_BASE") or "https://www.flashscorekz.com").rstrip("/")


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


def _normalize_stage(value: Any) -> str:
    text = " ".join(str(value or "").replace("-", " ").split())
    if not text:
        return ""

    low = text.lower().replace("ё", "е")
    m = re.fullmatch(r"1\s*/\s*(\d+)(?:\s+финала?)?", low)
    if m:
        return f"1/{m.group(1)} финала"

    m = re.search(r"round\s+of\s+(\d+)|last\s+(\d+)", low)
    if m:
        size = int(m.group(1) or m.group(2))
        if size > 1 and size % 2 == 0:
            return f"1/{size // 2} финала"

    if "quarter" in low or "четверть" in low:
        return "1/4 финала"
    if "semi" in low or "полуфин" in low:
        return "1/2 финала"
    if low in {"final", "финал"} or " финал" in low:
        return "финал"
    return text


def _stage_from_flashscore_page(event: Dict[str, Any]) -> str:
    raw = event.get("raw") or {}
    match_id = raw.get("flashscore_id") or event.get("custom_id")
    if not match_id:
        return ""
    url = f"{FLASHSCORE_BASE}/match/{match_id}/#/match-summary"
    try:
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "text/html,*/*",
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.5,en;q=0.4",
                "User-Agent": "Mozilla/5.0",
            },
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            page = resp.read().decode("utf-8", "replace")
        match = re.search(r'<meta\s+property=["\']og:description["\']\s+content=["\']([^"\']+)["\']', page, flags=re.I)
        if not match:
            return ""
        description = html.unescape(match.group(1))
        if " - " not in description:
            return ""
        return _normalize_stage(description.rsplit(" - ", 1)[1])
    except Exception:
        return ""


def _stage(event: Dict[str, Any]) -> str:
    raw = event.get("raw") or {}
    for key in ("card_stage", "flashscore_round", "round", "stage"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return _normalize_stage(value)
    return _stage_from_flashscore_page(event)


def _tour_line(event: Dict[str, Any]) -> str:
    custom = str(event.get("card_side_text") or "").strip()
    if custom:
        return custom
    category = str(event.get("tournament_status") or event.get("category") or "").upper()
    tournament = str(event.get("tournament_name") or "ТУРНИР").upper()
    if "(" in tournament:
        tournament = tournament.split("(", 1)[0].strip()
    if "," in tournament:
        tournament = tournament.split(",", 1)[0].strip()
    title = " ".join(x for x in (category, tournament) if x).strip()
    stage = _stage(event)
    return f"{title}\t{stage}".strip() if stage else title


def _score_columns(count: int) -> list[int]:
    return [816, 923, 1030] if count <= 3 else [709, 816, 923, 1030]


def _fallback_template(count: int):
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (W, H), (55, 28, 134, 255))
    px = img.load()
    for y in range(H):
        t = y / (H - 1)
        for x in range(W):
            wave = (x * 0.65 - y * 0.42) / W
            yellow = max(0.0, 1.0 - abs(wave + 0.25) * 3.2)
            grain = ((x * 17 + y * 23) % 37) - 18
            base = (55 + int(30 * t), 28 + int(24 * t), 134 + int(24 * (1 - t)))
            color = tuple(max(0, min(255, int(base[i] + (GREEN[i] - base[i]) * yellow + grain * 0.45))) for i in range(3))
            px[x, y] = color + (255,)

    draw = ImageDraw.Draw(img)
    draw.rectangle((LEFT_W, BOTTOM_Y, W, H), fill=PANEL)
    return img


def _base_template(count: int):
    from PIL import Image

    path = CARD_TEMPLATES[3 if count <= 3 else 4]
    if os.path.exists(path):
        try:
            img = Image.open(path).convert("RGBA")
            if img.size != (W, H):
                img = img.resize((W, H))
            return img
        except Exception:
            pass

    packed_path = os.path.splitext(path)[0] + ".b64"
    if os.path.exists(packed_path):
        try:
            with open(packed_path, "rb") as fh:
                img = Image.open(BytesIO(base64.b64decode(fh.read()))).convert("RGBA")
            if img.size != (W, H):
                img = img.resize((W, H))
            return img
        except Exception:
            pass
    return _fallback_template(count)


def _telegram_file_url(file_id: str) -> str:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token or not file_id:
        return ""
    try:
        payload = json.dumps({"file_id": file_id}).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/getFile",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        path = ((data or {}).get("result") or {}).get("file_path")
        if path:
            return f"https://api.telegram.org/file/bot{token}/{path}"
    except Exception:
        return ""
    return ""


def _photo_source(event: Dict[str, Any]) -> str:
    url = str(event.get("card_photo_url") or "").strip()
    if url.startswith(("http://", "https://")):
        return url
    file_id = str(event.get("card_photo_file_id") or "").strip()
    return _telegram_file_url(file_id)


def _overlay_photo(img: Any, event: Dict[str, Any]) -> None:
    from PIL import Image, ImageOps

    url = _photo_source(event)
    if not url:
        return
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            photo = Image.open(BytesIO(resp.read())).convert("RGBA")
        photo = ImageOps.fit(photo, (959, 1017), method=Image.Resampling.LANCZOS, centering=(0.5, 0.0))
        img.alpha_composite(photo, (W - 959, 0))
    except Exception as exc:
        print(f"[card] photo overlay failed: {exc}")


def _left_bar(img: Any, text: str) -> None:
    from PIL import Image, ImageDraw

    text = str(text or "").replace("\t", "    ").upper()
    font = _font("medium", 28)
    probe = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    bbox = ImageDraw.Draw(probe).textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tmp = Image.new("RGBA", (tw + 8, th + 8), (0, 0, 0, 0))
    d = ImageDraw.Draw(tmp)
    d.text((4 - bbox[0], 4 - bbox[1]), text, font=font, fill=WHITE)
    rotated = tmp.rotate(90, expand=True)
    x = max(0, LEFT_W - rotated.width - 12)
    img.alpha_composite(rotated, (x, 24))


def build_match_card_png(event: Dict[str, Any]) -> bytes:
    from PIL import ImageDraw

    home_scores, away_scores = _scores(event)
    col_count = min(4, max(3, len(home_scores), len(away_scores)))
    home_scores = (home_scores + [""] * col_count)[:col_count]
    away_scores = (away_scores + [""] * col_count)[:col_count]

    img = _base_template(col_count)
    _overlay_photo(img, event)
    draw = ImageDraw.Draw(img)
    _left_bar(img, _tour_line(event))

    name_x, y1, y2 = 122, 1118, 1228
    score_y1, score_y2 = 1114, 1224
    cols = _score_columns(col_count)

    home_name = _surname(str(event.get("card_home_name") or event.get("home_name") or "TBD"))
    away_name = _surname(str(event.get("card_away_name") or event.get("away_name") or "TBD"))
    winner = _winner(event)
    name_width = max(360, cols[0] - name_x - 55)
    draw.text((name_x, y1), home_name, font=_fit(draw, home_name, 76, name_width), fill=GREEN if winner == "home" else WHITE)
    draw.text((name_x, y2), away_name, font=_fit(draw, away_name, 76, name_width), fill=GREEN if winner == "away" else WHITE)

    score_font = _font("extra_italic", 76)
    for idx, value in enumerate(home_scores):
        _right(draw, cols[idx], score_y1, value, score_font, GREEN if idx == 0 and winner == "home" else WHITE)
    for idx, value in enumerate(away_scores):
        _right(draw, cols[idx], score_y2, value, score_font, GREEN if idx == 0 and winner == "away" else WHITE)

    out = BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()
