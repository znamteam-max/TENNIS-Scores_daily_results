from __future__ import annotations

import html
import re
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict

import tournament_session_store as store


UNKNOWN_LABELS = {"Стадия не определена", "Раунд не найден"}


def _stage_from_page(match: Dict[str, Any]) -> tuple[int, str]:
    """Read the round from the Flashscore match page.

    Flashscore exposes the round in page metadata/breadcrumb text, but not
    consistently in the daily score feed. Try the existing parser first,
    then inspect the returned HTML for the visible round label.
    """
    event_id = int(match.get("event_id") or 0)
    try:
        import match_card

        stage = store.normalize_stage(match_card._stage_from_flashscore_page(match))  # type: ignore[attr-defined]
        if stage:
            return event_id, stage

        raw = match.get("raw") or {}
        match_id = raw.get("flashscore_id") or match.get("custom_id")
        if not match_id:
            return event_id, ""

        base = str(getattr(match_card, "FLASHSCORE_BASE", "https://www.flashscorekz.com")).rstrip("/")
        urls = [
            f"{base}/match/{match_id}/#/match-summary",
            f"{base}/match/{match_id}/",
        ]
        headers = {
            "Accept": "text/html,*/*",
            "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/150 Safari/537.36",
        }
        for url in urls:
            try:
                request = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(request, timeout=6) as response:
                    page = response.read().decode("utf-8", "replace")
            except Exception:
                continue

            text = html.unescape(page).replace("\\/", "/").replace("\\u002F", "/")
            candidates = []
            candidates.extend(
                re.findall(
                    r"(?i)(?:^|[^0-9])1\s*/\s*(64|32|16|8|4|2)\s*[-–— ]*(?:finals?|финал(?:а)?)",
                    text,
                )
            )
            if candidates:
                return event_id, f"1/{candidates[0]} финала"
            if re.search(r"(?i)quarter[- ]?final|четвертьфин", text):
                return event_id, "1/4 финала"
            if re.search(r"(?i)semi[- ]?final|полуфин", text):
                return event_id, "1/2 финала"
            if re.search(r"(?i)(?:^|[> ,|·-])finals?(?:[< ,|·-]|$)|(?:^|[> ,|·-])финал(?:[< ,|·-]|$)", text):
                return event_id, "Финал"
            match_round = re.search(r"(?i)(?:round\s+of|last)\s+(128|64|32|16|8|4|2)", text)
            if match_round:
                size = int(match_round.group(1))
                return event_id, "Финал" if size == 2 else f"1/{size // 2} финала"
    except Exception:
        pass
    return event_id, ""


def install(module: Any) -> None:
    old_matches_menu = module._matches_menu
    old_callback = module._handle_callback

    def matches_menu(chat_id, group, tournament, day=None):
        markup = old_matches_menu(chat_id, group, tournament, day)
        rows = []
        for row in (markup or {}).get("inline_keyboard", []):
            updated = []
            for button in row:
                item = dict(button)
                if str(item.get("callback_data") or "").startswith("stage_detect|"):
                    item["text"] = "🔎 Определить раунды всех матчей"
                updated.append(item)
            rows.append(updated)
        return module._kb(rows)

    module._matches_menu = matches_menu

    def refresh(chat_id: int, message_id: int, group: str, tournament: str, day) -> None:
        module.set_state(
            chat_id,
            "picked_tournament",
            {"group": group, "tournament_name": tournament, "day": day.isoformat()},
        )
        module.tg_edit_message(
            chat_id,
            message_id,
            module._matches_title(chat_id, group, tournament, day),
            reply_markup=module._matches_menu(chat_id, group, tournament, day),
        )

    def callback(chat_id, message_id, cq_id, data, user_id=None):
        if not data.startswith("stage_detect|"):
            return old_callback(chat_id, message_id, cq_id, data, user_id=user_id)

        try:
            _, index_value = data.split("|", 1)
            group, tournament, day = module._current_choice(chat_id)
            groups = store.stage_groups(module, chat_id, group, tournament, day)
            index = int(index_value) - 1
            if index < 0 or index >= len(groups):
                raise ValueError("Список матчей изменился. Открой турнир заново.")
            _stage, matches = groups[index]
            targets = [
                match
                for match in matches
                if str(match.get("stage") or "") in UNKNOWN_LABELS
            ] or list(matches)
            if not targets:
                module.tg_answer_callback_query(cq_id, "У всех матчей раунд уже указан")
                return

            module.tg_answer_callback_query(cq_id, f"Проверяю {len(targets)} матчей...")
            module.tg_edit_message(
                chat_id,
                message_id,
                (
                    f"🔎 {tournament}\n"
                    f"Проверяю страницы всех матчей без раунда: {len(targets)}.\n"
                    "Это может занять несколько секунд."
                ),
            )

            detected: Dict[str, list[int]] = defaultdict(list)
            max_workers = min(8, max(1, len(targets)))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(_stage_from_page, match) for match in targets]
                for future in as_completed(futures):
                    event_id, stage = future.result()
                    if event_id and stage:
                        detected[stage].append(event_id)

            found = 0
            for stage, event_ids in detected.items():
                store.save_stage(event_ids, stage)
                found += len(event_ids)

            refresh(chat_id, message_id, group, tournament, day)
            unresolved = max(0, len(targets) - found)
            details = ", ".join(
                f"{stage}: {len(event_ids)}"
                for stage, event_ids in sorted(
                    detected.items(), key=lambda item: (store.STAGE_ORDER.get(item[0], 90), item[0])
                )
            )
            if found:
                text = f"Раунды считаны для {found}/{len(targets)} матчей"
                if details:
                    text += f": {details}."
                if unresolved:
                    text += f" Не удалось считать: {unresolved}."
                module.tg_send_message(chat_id, text)
            else:
                module.tg_send_message(
                    chat_id,
                    "Flashscore не отдал раунд ни для одного матча. Можно указать раунд вручную кнопками.",
                )
            return
        except Exception as exc:
            module.tg_answer_callback_query(cq_id, f"Ошибка: {exc}", show_alert=True)
            return

    module._handle_callback = callback
