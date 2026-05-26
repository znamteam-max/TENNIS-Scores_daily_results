from __future__ import annotations

import os
import re
import html
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
PANEL_FONT_MAX = 76
PANEL_FONT_MIN = 30
NAME_SCORE_GAP = 44
BO5_NAME_SCORE_GAP = 34
ROW1_CENTER_Y = 1161
ROW2_CENTER_Y = 1271
INTERRUPTED_STATUS_TOKENS = (
    "interrupted",
    "abandoned",
    "suspended",
    "\u043f\u0440\u0435\u0440\u0432",
    "\u043e\u0441\u0442\u0430\u043d\u043e\u0432",
)

FONT_URL = {
    "medium": "https://raw.githubusercontent.com/google/fonts/main/ofl/sofiasans/SofiaSans%5Bwght%5D.ttf",
    "italic": "https://raw.githubusercontent.com/google/fonts/main/ofl/sofiasans/SofiaSans-Italic%5Bwght%5D.ttf",
}
ROOT_DIR = os.path.dirname(__file__)
TEMPLATE_DIR = os.path.join(ROOT_DIR, "assets", "card_templates")
CARD_TEMPLATES = {
    3: os.path.join(TEMPLATE_DIR, "result_2_sets.png"),
    4: os.path.join(TEMPLATE_DIR, "result_3_sets.png"),
    5: os.path.join(TEMPLATE_DIR, "result_4_sets.png"),
    6: os.path.join(TEMPLATE_DIR, "result_5_sets.png"),
}
FONT_FILES = {
    "extra_italic": os.path.join(ROOT_DIR, "fonts", "SofiaSans-ExtraBoldItalic.ttf"),
}
FLASHSCORE_BASE = (os.getenv("FLASHSCORE_BASE") or "https://www.flashscorekz.com").rstrip("/")
GRAND_SLAM_TOURNAMENTS = (
    "australian open",
    "open australia",
    "открытый чемпионат австралии",
    "австралия open",
    "roland garros",
    "french open",
    "открытый чемпионат франции",
    "ролан гаррос",
    "wimbledon",
    "уимблдон",
    "us open",
    "открытый чемпионат сша",
)
GRAND_SLAM_DISPLAY_TITLES = (
    (("australian open", "open australia", "открытый чемпионат австралии", "австралия open"), "Australian Open"),
    (("roland garros", "french open", "открытый чемпионат франции", "ролан гаррос"), "Ролан Гаррос"),
    (("wimbledon", "уимблдон"), "Wimbledon"),
    (("us open", "открытый чемпионат сша"), "US Open"),
)


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


def _fit_many(draw: Any, texts: list[str], size: int, width: int):
    font = _font("extra_italic", size)
    while size > 26 and any(_size(draw, text, font)[0] > width for text in texts if text):
        size -= 2
        font = _font("extra_italic", size)
    return font


def _is_pair_name(name: str) -> bool:
    return "/" in str(name or "")


def _right(draw: Any, x: int, y: int, text: str, font: Any, fill: tuple[int, int, int, int]) -> None:
    tw, _ = _size(draw, text, font)
    draw.text((x - tw, y), text, font=font, fill=fill)


def _center(draw: Any, x: int, y: int, text: str, font: Any, fill: tuple[int, int, int, int]) -> None:
    box = draw.textbbox((0, 0), str(text), font=font)
    tw = box[2] - box[0]
    draw.text((int(round(x - tw / 2 - box[0])), y), str(text), font=font, fill=fill)


def _row_y(draw: Any, text: str, font: Any, center_y: int) -> int:
    box = draw.textbbox((0, 0), str(text), font=font)
    return int(round(center_y - (box[1] + box[3]) / 2))


def _left_row(draw: Any, x: int, center_y: int, text: str, font: Any, fill: tuple[int, int, int, int]) -> None:
    draw.text((x, _row_y(draw, text, font, center_y)), str(text), font=font, fill=fill)


def _center_row(draw: Any, x: int, center_y: int, text: str, font: Any, fill: tuple[int, int, int, int]) -> None:
    box = draw.textbbox((0, 0), str(text), font=font)
    draw.text(
        (
            int(round(x - (box[0] + box[2]) / 2)),
            int(round(center_y - (box[1] + box[3]) / 2)),
        ),
        str(text),
        font=font,
        fill=fill,
    )


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


def _norm(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("ё", "е").split())


def _is_grand_slam(event: Dict[str, Any]) -> bool:
    try:
        if int(event.get("tournament_sort_rank", 9)) == 0:
            return True
    except Exception:
        pass
    hay = _norm(
        " ".join(
            str(part or "")
            for part in (
                event.get("tournament_status"),
                event.get("tournament_name"),
                event.get("season_name"),
                event.get("category"),
            )
        )
    )
    return "grand slam" in hay or any(name in hay for name in GRAND_SLAM_TOURNAMENTS)


def _is_mens_grand_slam(event: Dict[str, Any]) -> bool:
    group = str(event.get("tour_group") or "").lower()
    category = str(event.get("category") or "").upper()
    return _is_grand_slam(event) and (group == "men" or category.startswith("ATP"))


def _looks_best_of_five(event: Dict[str, Any]) -> bool:
    home, away = _score(event, "home"), _score(event, "away")
    for idx in (4, 5):
        if _val(home, f"period{idx}") != "" and _val(away, f"period{idx}") != "":
            return True
    for scores in (event.get("card_home_scores"), event.get("card_away_scores")):
        if isinstance(scores, list) and len(scores) >= 5:
            return True
    try:
        home_total = int(float(_val(home, "current", "display") or 0))
        away_total = int(float(_val(away, "current", "display") or 0))
    except Exception:
        return False
    return max(home_total, away_total) >= 3 or home_total + away_total > 3


def _score_limit(event: Dict[str, Any]) -> int:
    return 6 if _is_mens_grand_slam(event) or _looks_best_of_five(event) else 4


def _scores(event: Dict[str, Any]) -> tuple[list[str], list[str]]:
    limit = _score_limit(event)
    custom_home = event.get("card_home_scores")
    custom_away = event.get("card_away_scores")
    if isinstance(custom_home, list) and isinstance(custom_away, list) and custom_home and custom_away:
        if limit <= 4 or min(len(custom_home), len(custom_away)) >= limit:
            return [str(x) for x in custom_home[:limit]], [str(x) for x in custom_away[:limit]]

    home, away = _score(event, "home"), _score(event, "away")
    h = [_fmt(_val(home, "current", "display"))]
    a = [_fmt(_val(away, "current", "display"))]
    for idx in range(1, 6):
        hv, av = _val(home, f"period{idx}"), _val(away, f"period{idx}")
        if hv == "" or av == "":
            continue
        h.append(_fmt(hv))
        a.append(_fmt(av))
    return h[:limit], a[:limit]


def _status_type(event: Dict[str, Any]) -> str:
    raw = event.get("raw") or {}
    status = raw.get("status") or {}
    value = str(event.get("status_type") or (status.get("type") or "")).lower()
    text = " ".join(str(part or "").lower() for part in (value, status.get("detail"), status.get("description"), raw.get("statusDescription"), raw.get("note")))
    if any(token in text for token in INTERRUPTED_STATUS_TOKENS):
        return "interrupted"
    return value


def _has_result_winner(event: Dict[str, Any]) -> bool:
    return _status_type(event) in {"finished", "retired", "walkover"}


def _winner(event: Dict[str, Any]) -> str:
    manual = str(event.get("card_winner_side") or "").lower()
    if manual in {"home", "away"}:
        return manual
    if manual == "none":
        return ""
    if not _has_result_winner(event):
        return ""
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
    raw_tournament = str(event.get("tournament_name") or "ТУРНИР").strip()
    tournament = raw_tournament.upper()
    if "(" in tournament:
        tournament = tournament.split("(", 1)[0].strip()
    if "," in tournament:
        tournament = tournament.split(",", 1)[0].strip()
    if _is_grand_slam(event):
        title = _grand_slam_display_title(event, tournament)
        stage = _stage(event)
        return f"{title}\t{stage}".strip() if stage else title
    title = " ".join(x for x in (category, tournament) if x).strip()
    stage = _stage(event)
    return f"{title}\t{stage}".strip() if stage else title


def _grand_slam_display_title(event: Dict[str, Any], fallback: str) -> str:
    hay = _norm(
        " ".join(
            str(part or "")
            for part in (
                event.get("tournament_name"),
                event.get("season_name"),
                event.get("tournament_status"),
                event.get("category"),
            )
        )
    )
    for needles, title in GRAND_SLAM_DISPLAY_TITLES:
        if any(needle in hay for needle in needles):
            return title
    return fallback


def _score_columns(count: int) -> list[int]:
    if count <= 3:
        return [816, 923, 1030]
    if count == 4:
        return [709, 824, 927, 1030]
    if count == 5:
        return [635, 742, 849, 956, 1030]
    return [580, 672, 762, 854, 944, 1030]


def _score_centers(count: int) -> list[int]:
    if count <= 3:
        return [795, 902, 1009]
    if count == 4:
        return [696, 803, 910, 1007]
    if count == 5:
        return [584, 690, 797, 904, 1011]
    return [535, 626, 717, 808, 899, 990]


def _score_center_x(x: int, value: str) -> int:
    return x + 5 if len(str(value)) > 1 else x


def _score_font(draw: Any, values: list[str], count: int):
    cleaned = [str(value) for value in values if str(value)]
    size = 64 if any(len(value) > 1 for value in cleaned) else 76
    font = _font("extra_italic", size)
    if count >= 6:
        max_width = 58 if any(len(value) > 1 for value in cleaned) else 74
    elif count == 5:
        max_width = 62 if any(len(value) > 1 for value in cleaned) else 82
    else:
        max_width = 62 if any(len(value) > 1 for value in cleaned) else 90
    while size > 52 and any(_size(draw, value, font)[0] > max_width for value in cleaned):
        size -= 2
        font = _font("extra_italic", size)
    return font


def _score_value_width_limit(values: list[str], count: int) -> int:
    cleaned = [str(value) for value in values if str(value)]
    has_wide_value = any(len(value) > 1 for value in cleaned)
    if count >= 6:
        return 58 if has_wide_value else 74
    if count == 5:
        return 62 if has_wide_value else 82
    return 62 if has_wide_value else 90


def _panel_font(
    draw: Any,
    names: list[str],
    score_rows: list[list[str]],
    count: int,
    centers: list[int],
    name_x: int,
):
    score_values = [str(value) for row in score_rows for value in row if str(value)]
    first_score_values = [str(row[0]) for row in score_rows if row and str(row[0])]
    score_limit = _score_value_width_limit(score_values, count)

    for size in range(PANEL_FONT_MAX, PANEL_FONT_MIN - 1, -2):
        font = _font("extra_italic", size)
        if any(_size(draw, value, font)[0] > score_limit for value in score_values):
            continue

        first_score_width = max((_size(draw, value, font)[0] for value in first_score_values), default=0)
        gap = BO5_NAME_SCORE_GAP if count >= 6 else NAME_SCORE_GAP
        name_width = centers[0] - first_score_width / 2 - gap - name_x
        if all(_size(draw, name, font)[0] <= name_width for name in names if name):
            return font

    return _font("extra_italic", PANEL_FONT_MIN)


def _base_template(count: int):
    from PIL import Image

    template_key = min(6, max(3, count))
    path = CARD_TEMPLATES[template_key]
    if not os.path.exists(path):
        raise FileNotFoundError(f"card template not found: {path}")

    img = Image.open(path)
    if img.size != (W, H):
        raise ValueError(f"card template has wrong size: {img.size}, expected {(W, H)}")
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    else:
        img = img.copy()
    return img


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
        photo = ImageOps.fit(photo, (W - LEFT_W, BOTTOM_Y), method=Image.Resampling.LANCZOS, centering=(0.5, 0.0))
        img.alpha_composite(photo, (LEFT_W, 0))
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
    col_count = min(_score_limit(event), max(3, len(home_scores), len(away_scores)))
    home_scores = (home_scores + [""] * col_count)[:col_count]
    away_scores = (away_scores + [""] * col_count)[:col_count]

    img = _base_template(col_count)
    _overlay_photo(img, event)
    draw = ImageDraw.Draw(img)
    _left_bar(img, _tour_line(event))

    name_x = 98 if col_count >= 6 else 122
    centers = _score_centers(col_count)

    home_name = _surname(str(event.get("card_home_name") or event.get("home_name") or "TBD"))
    away_name = _surname(str(event.get("card_away_name") or event.get("away_name") or "TBD"))
    winner = _winner(event)
    if winner == "away":
        top_name, bottom_name = away_name, home_name
        top_scores, bottom_scores = away_scores, home_scores
        top_winner, bottom_winner = True, False
    else:
        top_name, bottom_name = home_name, away_name
        top_scores, bottom_scores = home_scores, away_scores
        top_winner, bottom_winner = winner == "home", False

    panel_font = _panel_font(draw, [top_name, bottom_name], [top_scores, bottom_scores], col_count, centers, name_x)
    _left_row(draw, name_x, ROW1_CENTER_Y, top_name, panel_font, GREEN if top_winner else WHITE)
    _left_row(draw, name_x, ROW2_CENTER_Y, bottom_name, panel_font, GREEN if bottom_winner else WHITE)

    for idx, value in enumerate(top_scores):
        _center_row(draw, _score_center_x(centers[idx], value), ROW1_CENTER_Y, value, panel_font, GREEN if idx == 0 and top_winner else WHITE)
    for idx, value in enumerate(bottom_scores):
        _center_row(draw, _score_center_x(centers[idx], value), ROW2_CENTER_Y, value, panel_font, GREEN if idx == 0 and bottom_winner else WHITE)

    out = BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()
