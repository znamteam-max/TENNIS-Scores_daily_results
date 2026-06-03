from __future__ import annotations

import html
import json
import re
import time
import unicodedata
import uuid
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Union

from match_card import build_match_card_png
from providers import sofascore as ss

try:
    from db_pg import ru_name_for, save_result_card, set_alias
except Exception:  # pragma: no cover - keeps local rendering usable without DB env
    ru_name_for = None
    save_result_card = None
    set_alias = None


def _api_url(bot_token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{bot_token}/{method}"


def _decode_response(raw: bytes) -> Dict[str, Any]:
    if not raw:
        return {"ok": True}
    try:
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {"ok": True, "result": data}
    except Exception:
        return {"ok": True}


def _post_json(url: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return _decode_response(resp.read())
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:
            body = "<body read failed>"
        print(f"[tg] request failed: status={exc.code} body={body}")
    except urllib.error.URLError as exc:
        print(f"[tg] request failed: {exc}")
    return None


def _post_multipart(
    url: str,
    fields: Dict[str, Any],
    file_field: str,
    filename: str,
    content_type: str,
    file_bytes: bytes,
) -> Optional[Dict[str, Any]]:
    boundary = f"----tennis-card-{uuid.uuid4().hex}"
    chunks: list[bytes] = []

    for name, value in fields.items():
        if value is None:
            continue
        chunks.append(f"--{boundary}\r\n".encode("ascii"))
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("ascii"))
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")

    chunks.append(f"--{boundary}\r\n".encode("ascii"))
    file_header = (
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("ascii")
    chunks.append(file_header)
    chunks.append(file_bytes)
    chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("ascii"))

    body = b"".join(chunks)
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "Content-Length": str(len(body))},
    )
    try:
        with urllib.request.urlopen(req, timeout=75) as resp:
            data = _decode_response(resp.read())
            print(f"[tg] media response ok={data.get('ok')} keys={list(data.keys())}")
            return data
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8", "replace")
        except Exception:
            body_text = "<body read failed>"
        print(f"[tg] media failed: status={exc.code} body={body_text}")
    except urllib.error.URLError as exc:
        print(f"[tg] media failed: {exc}")
    return None


def _has_cyrillic(text: str) -> bool:
    return any("а" <= ch.lower() <= "я" or ch.lower() == "ё" for ch in text)


def _strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def _slug(text: str) -> str:
    cleaned = _strip_accents(text).lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", cleaned).strip("-")
    return cleaned


def _sports_ru_name(name: str) -> str:
    parts = [p for p in re.split(r"\s+", _strip_accents(name).strip()) if p and not (len(p.rstrip(".")) == 1 and p.rstrip(".").isalpha())]
    if not parts:
        return ""
    candidates = []
    candidates.append(_slug(" ".join(parts)))
    if len(parts) >= 2:
        candidates.append(_slug(f"{parts[-1]} {' '.join(parts[:-1])}"))
    candidates.append(_slug(parts[0]))

    for slug in dict.fromkeys(x for x in candidates if x):
        url = f"https://www.sports.ru/tennis/person/{urllib.parse.quote(slug)}/"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                page = resp.read().decode("utf-8", "replace")
            match = re.search(r"<h1[^>]*>(.*?)</h1>", page, flags=re.I | re.S)
            if not match:
                match = re.search(r"<title>(.*?):", page, flags=re.I | re.S)
            if not match:
                continue
            value = html.unescape(re.sub(r"<[^>]+>", " ", match.group(1)))
            value = " ".join(value.split())
            if value and _has_cyrillic(value):
                return value
        except Exception:
            continue
    return ""


def _person_title(title: str) -> str:
    title = " ".join((title or "").split())
    if not title:
        return ""
    title = title.split("(", 1)[0].strip()
    if "," in title:
        surname, rest = [x.strip() for x in title.split(",", 1)]
        first = (rest.split() or [""])[0]
        return " ".join(x for x in (first, surname) if x)
    return title


def _wikipedia_ru_name(name: str) -> str:
    query = urllib.parse.urlencode(
        {
            "action": "query",
            "list": "search",
            "srsearch": f"{name} теннис",
            "format": "json",
            "utf8": "1",
            "srlimit": "3",
        }
    )
    url = f"https://ru.wikipedia.org/w/api.php?{query}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "tennis-scores-bot/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        for row in ((data.get("query") or {}).get("search") or []):
            title = _person_title(str(row.get("title") or ""))
            if title and _has_cyrillic(title):
                return title
    except Exception:
        return ""
    return ""


def _latin_to_ru(text: str) -> str:
    combos = [
        ("shch", "щ"),
        ("sch", "ш"),
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
    src = _strip_accents(text).lower()
    i = 0
    while i < len(src):
        ch = src[i]
        if not ("a" <= ch <= "z"):
            out.append(ch)
            i += 1
            continue
        for latin, ru in combos:
            if src.startswith(latin, i):
                out.append(ru)
                i += len(latin)
                break
        else:
            out.append(chars.get(ch, ch))
            i += 1
    return "".join(out)


def _ru_name(name: Any) -> str:
    raw = " ".join(str(name or "").split())
    if not raw:
        return raw
    if ru_name_for:
        try:
            alias, ok = ru_name_for(raw)
            if ok and alias:
                return alias
        except Exception as exc:
            print(f"[names] alias lookup failed: {exc}")
    if _has_cyrillic(raw):
        return raw
    sports = _sports_ru_name(raw)
    if sports:
        if set_alias:
            try:
                set_alias(raw, sports)
            except Exception as exc:
                print(f"[names] sports alias save failed: {exc}")
        return sports
    wiki = _wikipedia_ru_name(raw)
    if wiki:
        if set_alias:
            try:
                set_alias(raw, wiki)
            except Exception as exc:
                print(f"[names] wiki alias save failed: {exc}")
        return wiki
    fallback = _latin_to_ru(raw).title()
    if set_alias and fallback:
        try:
            set_alias(raw, fallback)
        except Exception as exc:
            print(f"[names] fallback alias save failed: {exc}")
    return fallback


def _card_event(event: Dict[str, Any]) -> Dict[str, Any]:
    data = json.loads(json.dumps(event, ensure_ascii=False, default=str))
    data.setdefault("card_original_home_name", data.get("home_name") or "")
    data.setdefault("card_original_away_name", data.get("away_name") or "")
    data["home_name"] = _ru_name(data.get("home_name"))
    data["away_name"] = _ru_name(data.get("away_name"))
    return data


def _review_markup(card_id: str) -> Dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "все ок", "callback_data": f"card_ok|{card_id}"},
                {"text": "исправить", "callback_data": f"card_fix|{card_id}"},
            ]
        ]
    }


ChatId = Union[int, str]


def _response_message_ref(response: Optional[Dict[str, Any]]) -> Optional[Dict[str, int]]:
    result = (response or {}).get("result")
    if not isinstance(result, dict):
        return None
    chat = result.get("chat") or {}
    chat_id = chat.get("id")
    message_id = result.get("message_id")
    try:
        return {"chat_id": int(chat_id), "message_id": int(message_id)}
    except Exception:
        return None


def _chat_id_from_response(response: Optional[Dict[str, Any]]) -> Optional[int]:
    ref = _response_message_ref(response)
    return int(ref["chat_id"]) if ref else None


def _to_int_chat_id(value: Optional[ChatId]) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except Exception:
        return None


def _delete_previous_messages(bot_token: str, event: Dict[str, Any]) -> None:
    refs = event.get("telegram_message_refs") or []
    if not isinstance(refs, list):
        return
    seen: set[tuple[int, int]] = set()
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        try:
            chat_id = int(ref.get("chat_id"))
            message_id = int(ref.get("message_id"))
        except Exception:
            continue
        key = (chat_id, message_id)
        if key in seen:
            continue
        seen.add(key)
        _post_json(_api_url(bot_token, "deleteMessage"), {"chat_id": chat_id, "message_id": message_id})


def _send_review_menu(bot_token: str, chat_id: ChatId, card_id: str) -> Optional[Dict[str, Any]]:
    return _post_json(
        _api_url(bot_token, "sendMessage"),
        {
            "chat_id": chat_id,
            "text": "Плашка опубликована. Проверить?",
            "reply_markup": _review_markup(card_id),
        },
    )


def send_match_result(
    bot_token: str,
    chat_id: ChatId,
    event: Dict[str, Any],
    review_chat_id: Optional[ChatId] = None,
    review_in_publish_chat: bool = False,
    allow_text_fallback: bool = False,
    allow_incomplete_card: bool = False,
    card_id: Optional[str] = None,
    delete_previous: bool = False,
) -> bool:
    if not bot_token:
        return False

    card_id = (card_id or event.get("telegram_card_id") or "").strip() or uuid.uuid4().hex[:12]
    review_chat_id = review_chat_id if review_chat_id is not None else None
    event = _card_event(event)
    if not allow_incomplete_card and not ss.has_result_winner(event):
        print(
            "[card] skip result card without completed winner "
            f"event_id={event.get('event_id')} status={ss.status_type(event)}"
        )
        return False
    previous_refs = event.get("telegram_message_refs") or []
    text = ss.result_message(event, include_stats=False)
    stats_text = ss.stats_message(event)
    try:
        t0 = time.monotonic()
        png = build_match_card_png(event)
        print(f"[card] png rendered bytes={len(png)} elapsed={time.monotonic() - t0:.2f}s event_id={event.get('event_id')}")
        caption: Optional[str] = text if len(text) <= 1000 else None
        message_refs: list[Dict[str, int]] = []
        t1 = time.monotonic()
        document = _post_multipart(
            _api_url(bot_token, "sendDocument"),
            {"chat_id": chat_id, "caption": caption},
            "document",
            "match-result.png",
            "image/png",
            png,
        )
        print(f"[card] sendDocument elapsed={time.monotonic() - t1:.2f}s ok={document.get('ok') if document else None}")
        if document and document.get("ok", True):
            document_ref = _response_message_ref(document)
            if document_ref:
                message_refs.append(document_ref)
            storage_chat_id = (
                _chat_id_from_response(document)
                or _to_int_chat_id(review_chat_id)
                or _to_int_chat_id(chat_id)
            )
            if review_in_publish_chat and storage_chat_id is not None:
                review_storage_chat_id = storage_chat_id
                review_destination: ChatId = storage_chat_id
            else:
                review_storage_chat_id = _to_int_chat_id(review_chat_id) or storage_chat_id or _to_int_chat_id(chat_id)
                review_destination = (
                    review_chat_id
                    if review_chat_id is not None
                    else (storage_chat_id if storage_chat_id is not None else chat_id)
                )
            if save_result_card:
                try:
                    if review_storage_chat_id is not None:
                        event["telegram_card_id"] = card_id
                        event["telegram_chat_id"] = review_storage_chat_id
                        event["telegram_publish_chat_id"] = storage_chat_id
                        event["telegram_message_refs"] = message_refs
                        save_result_card(card_id, review_storage_chat_id, event)
                except Exception as exc:
                    print(f"[card] save failed: {exc}")
            if caption is None:
                text_response = _post_json(_api_url(bot_token, "sendMessage"), {"chat_id": chat_id, "text": text})
                text_ref = _response_message_ref(text_response)
                if text_ref:
                    message_refs.append(text_ref)
            if stats_text:
                stats_response = _post_json(_api_url(bot_token, "sendMessage"), {"chat_id": chat_id, "text": stats_text})
                stats_ref = _response_message_ref(stats_response)
                if stats_ref:
                    message_refs.append(stats_ref)
            review_response = _send_review_menu(bot_token, review_destination, card_id)
            review_ref = _response_message_ref(review_response)
            if review_ref:
                message_refs.append(review_ref)
            if delete_previous or previous_refs:
                _delete_previous_messages(bot_token, {"telegram_message_refs": previous_refs})
            if save_result_card and review_storage_chat_id is not None:
                try:
                    event["telegram_message_refs"] = message_refs
                    save_result_card(card_id, review_storage_chat_id, event)
                except Exception as exc:
                    print(f"[card] save message refs failed: {exc}")
            return True
        if document:
            print(f"[card] sendDocument failed response={document}")
    except Exception as exc:
        print(f"[card] render/send failed: {exc}")

    if not allow_text_fallback:
        failure_chat_id: ChatId = review_chat_id if review_chat_id is not None else chat_id
        _post_json(
            _api_url(bot_token, "sendMessage"),
            {
                "chat_id": failure_chat_id,
                "text": "Не смог отправить новую плашку. Старую версию не удалял, попробуй прислать фото меньшего размера или повторить позже.",
            },
        )
        return False
    return bool(_post_json(_api_url(bot_token, "sendMessage"), {"chat_id": chat_id, "text": text}))
