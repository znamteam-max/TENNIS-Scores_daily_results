from __future__ import annotations

import copy
import datetime as dt
import time
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List
from zoneinfo import ZoneInfo

import tournament_session_store as store
from providers import sofascore as ss


_PROVIDER_INSTALLED = False
_STORE_INSTALLED = False
_VALIDATION_INSTALLED = False
_SUMMARY_INSTALLED = False

_DAVIS_TOKENS = (
    "davis cup",
    "кубок дэвиса",
    "copa davis",
)

_FINALS_TOKENS = (
    ("atp finals", "ATP Finals"),
    ("итоговый турнир atp", "ATP Finals"),
    ("wta finals", "WTA Finals"),
    ("итоговый турнир wta", "WTA Finals"),
)

# Canonical code -> (Russian display name, IANA timezone, aliases).
_COUNTRIES: Dict[str, tuple[str, str, tuple[str, ...]]] = {
    "ARG": ("Аргентина", "America/Argentina/Buenos_Aires", ("arg", "argentina", "аргентина")),
    "AUS": ("Австралия", "Australia/Sydney", ("aus", "australia", "австралия")),
    "AUT": ("Австрия", "Europe/Vienna", ("aut", "austria", "австрия")),
    "BEL": ("Бельгия", "Europe/Brussels", ("bel", "belgium", "бельгия")),
    "BER": ("Бермуды", "Atlantic/Bermuda", ("ber", "bermuda", "бермуды")),
    "BIH": ("Босния и Герцеговина", "Europe/Sarajevo", ("bih", "bosnia", "bosnia and herzegovina", "босния")),
    "BOL": ("Боливия", "America/La_Paz", ("bol", "bolivia", "боливия")),
    "BRA": ("Бразилия", "America/Sao_Paulo", ("bra", "brazil", "brasil", "бразилия")),
    "BUL": ("Болгария", "Europe/Sofia", ("bul", "bulgaria", "болгария")),
    "CAN": ("Канада", "America/Toronto", ("can", "canada", "канада")),
    "CHI": ("Чили", "America/Santiago", ("chi", "chl", "chile", "чили")),
    "CHN": ("Китай", "Asia/Shanghai", ("chn", "china", "china pr", "китай")),
    "COL": ("Колумбия", "America/Bogota", ("col", "colombia", "колумбия")),
    "CRO": ("Хорватия", "Europe/Zagreb", ("cro", "croatia", "хорватия")),
    "CYP": ("Кипр", "Asia/Nicosia", ("cyp", "cyprus", "кипр")),
    "CZE": ("Чехия", "Europe/Prague", ("cze", "czechia", "czech republic", "чехия")),
    "DEN": ("Дания", "Europe/Copenhagen", ("den", "denmark", "дания")),
    "DOM": ("Доминиканская Республика", "America/Santo_Domingo", ("dom", "dominican republic", "доминиканская республика")),
    "ECU": ("Эквадор", "America/Guayaquil", ("ecu", "ecuador", "эквадор")),
    "EGY": ("Египет", "Africa/Cairo", ("egy", "egypt", "египет")),
    "ESA": ("Сальвадор", "America/El_Salvador", ("esa", "el salvador", "сальвадор")),
    "EST": ("Эстония", "Europe/Tallinn", ("est", "estonia", "эстония")),
    "FIN": ("Финляндия", "Europe/Helsinki", ("fin", "finland", "финляндия")),
    "FRA": ("Франция", "Europe/Paris", ("fra", "france", "франция")),
    "GBR": ("Великобритания", "Europe/London", ("gbr", "great britain", "united kingdom", "британия", "великобритания")),
    "GER": ("Германия", "Europe/Berlin", ("ger", "deu", "germany", "германия")),
    "GRE": ("Греция", "Europe/Athens", ("gre", "greece", "греция")),
    "HUN": ("Венгрия", "Europe/Budapest", ("hun", "hungary", "венгрия")),
    "IND": ("Индия", "Asia/Kolkata", ("ind", "india", "индия")),
    "JPN": ("Япония", "Asia/Tokyo", ("jpn", "japan", "япония")),
    "KAZ": ("Казахстан", "Asia/Almaty", ("kaz", "kazakhstan", "казахстан")),
    "KOR": ("Южная Корея", "Asia/Seoul", ("kor", "korea republic", "south korea", "южная корея", "корея")),
    "LTU": ("Литва", "Europe/Vilnius", ("ltu", "lithuania", "литва")),
    "LUX": ("Люксембург", "Europe/Luxembourg", ("lux", "luxembourg", "люксембург")),
    "MAR": ("Марокко", "Africa/Casablanca", ("mar", "morocco", "марокко")),
    "MEX": ("Мексика", "America/Mexico_City", ("mex", "mexico", "мексика")),
    "MON": ("Монако", "Europe/Monaco", ("mon", "monaco", "монако")),
    "NED": ("Нидерланды", "Europe/Amsterdam", ("ned", "netherlands", "holland", "нидерланды", "голландия")),
    "NGR": ("Нигерия", "Africa/Lagos", ("ngr", "nigeria", "нигерия")),
    "NOR": ("Норвегия", "Europe/Oslo", ("nor", "norway", "норвегия")),
    "NZL": ("Новая Зеландия", "Pacific/Auckland", ("nzl", "new zealand", "новая зеландия")),
    "PAR": ("Парагвай", "America/Asuncion", ("par", "pry", "paraguay", "парагвай")),
    "PER": ("Перу", "America/Lima", ("per", "peru", "перу")),
    "POL": ("Польша", "Europe/Warsaw", ("pol", "poland", "польша")),
    "POR": ("Португалия", "Europe/Lisbon", ("por", "portugal", "португалия")),
    "RSA": ("ЮАР", "Africa/Johannesburg", ("rsa", "zaf", "south africa", "юар", "южная африка")),
    "SRB": ("Сербия", "Europe/Belgrade", ("srb", "serbia", "сербия")),
    "SUI": ("Швейцария", "Europe/Zurich", ("sui", "che", "switzerland", "швейцария")),
    "SVK": ("Словакия", "Europe/Bratislava", ("svk", "slovakia", "словакия")),
    "SWE": ("Швеция", "Europe/Stockholm", ("swe", "sweden", "швеция")),
    "THA": ("Таиланд", "Asia/Bangkok", ("tha", "thailand", "таиланд")),
    "TPE": ("Китайский Тайбэй", "Asia/Taipei", ("tpe", "twn", "chinese taipei", "taiwan", "тайвань", "китайский тайбэй")),
    "TUR": ("Турция", "Europe/Istanbul", ("tur", "turkiye", "turkey", "турция")),
    "UKR": ("Украина", "Europe/Kyiv", ("ukr", "ukraine", "украина")),
    "URU": ("Уругвай", "America/Montevideo", ("uru", "uruguay", "уругвай")),
    "USA": ("США", "America/New_York", ("usa", "united states", "united states of america", "сша")),
    "ESP": ("Испания", "Europe/Madrid", ("esp", "spain", "испания")),
}

_ALIAS_TO_CODE: Dict[str, str] = {}
for _code, (_display, _tz, _aliases) in _COUNTRIES.items():
    _ALIAS_TO_CODE[_code.lower()] = _code
    for _alias in _aliases:
        _ALIAS_TO_CODE[_alias.lower()] = _code


def _norm(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("ё", "е").replace(".", " ").split())


def _hay(event: Dict[str, Any]) -> str:
    raw = event.get("raw") or {}
    tournament = raw.get("tournament") or {}
    unique = tournament.get("uniqueTournament") or {}
    return _norm(
        " ".join(
            str(x or "")
            for x in (
                event.get("tournament_source_name"),
                event.get("tournament_name"),
                event.get("season_name"),
                event.get("tournament_status"),
                raw.get("flashscore_league"),
                raw.get("eventName"),
                tournament.get("name"),
                unique.get("name"),
            )
        )
    )


def is_davis_cup_event(event: Dict[str, Any]) -> bool:
    text = _hay(event)
    return any(token in text for token in _DAVIS_TOKENS)


def _country_code(value: Any) -> str:
    text = _norm(value)
    if not text:
        return ""
    if text in _ALIAS_TO_CODE:
        return _ALIAS_TO_CODE[text]
    compact = text.replace(" ", "")
    if compact in _ALIAS_TO_CODE:
        return _ALIAS_TO_CODE[compact]
    return ""


def _side_country(event: Dict[str, Any], side: str) -> str:
    raw = event.get("raw") or {}
    competitor = raw.get("homeCompetitor" if side == "home" else "awayCompetitor") or {}
    values: List[Any] = []
    countries = competitor.get("countries")
    if isinstance(countries, list):
        values.extend(countries)
    values.append(competitor.get("country"))
    for value in values:
        code = _country_code(value)
        if code:
            return code
    return ""


def _country_display(code: str) -> str:
    return _COUNTRIES.get(code, (code, "", ()))[0] if code else ""


def _country_tz(code: str) -> str:
    return _COUNTRIES.get(code, ("", "", ()))[1] if code else ""


def _round_label(event: Dict[str, Any]) -> str:
    text = _hay(event)
    raw = event.get("raw") or {}

    # Final 8 has real knockout rounds, so keep an explicit provider round if present.
    if "final 8" in text or "финал 8" in text:
        for value in (event.get("stage"), raw.get("flashscore_round"), raw.get("round"), raw.get("stage")):
            stage = store.normalize_stage(value)
            if stage in {"1/4 финала", "1/2 финала", "Финал"}:
                return stage
        return "Final 8"

    if "qualif" in text or "квалиф" in text:
        if any(token in text for token in ("round 2", "2nd round", "2-й раунд", "2 раунд")):
            return "Квалификация · 2-й раунд"
        if any(token in text for token in ("round 1", "1st round", "1-й раунд", "1 раунд")):
            return "Квалификация · 1-й раунд"
        return "Квалификация"

    if "group ii" in text or "группа ii" in text or "группа 2" in text:
        if "play" in text or "плей" in text:
            return "Плей-офф Мировой группы II"
        return "Мировая группа II · Раунд 1"

    if "group i" in text or "группа i" in text or "группа 1" in text:
        if "play" in text or "плей" in text:
            return "Плей-офф Мировой группы I"
        return "Мировая группа I · Раунд 1"

    if "group iii" in text or "группа iii" in text or "группа 3" in text:
        return "Региональная группа III"
    if "group iv" in text or "группа iv" in text or "группа 4" in text:
        return "Региональная группа IV"
    if "group v" in text or "группа v" in text or "группа 5" in text:
        return "Региональная группа V"

    return "Кубок Дэвиса"


def _tie_context(event: Dict[str, Any]) -> tuple[str, str, str, str]:
    home = _side_country(event, "home")
    away = _side_country(event, "away")
    keys = sorted({code for code in (home, away) if code})
    tie_key = "|".join(keys)
    tie_label = ""
    if len(keys) == 2:
        tie_label = " — ".join(_country_display(code) for code in keys)
    elif len(keys) == 1:
        tie_label = _country_display(keys[0])
    return home, away, tie_key, tie_label


def decorate_event(event: Dict[str, Any]) -> Dict[str, Any]:
    # Finals are also top-level events, not ATP/WTA 250.
    text = _hay(event)
    for token, label in _FINALS_TOKENS:
        if token in text:
            event["tournament_status"] = label
            event["tournament_sort_rank"] = 0
            break

    if not is_davis_cup_event(event):
        return event

    source = str(event.get("tournament_source_name") or event.get("tournament_name") or "Кубок Дэвиса")
    home, away, tie_key, tie_label = _tie_context(event)
    round_label = _round_label(event)
    stage = f"{round_label} · {tie_label}" if tie_label else round_label

    event["team_event"] = True
    event["team_event_type"] = "davis_cup"
    event["tournament_source_name"] = source
    event["tournament_status"] = "Кубок Дэвиса"
    event["tournament_sort_rank"] = 0
    event["team_home_country"] = home
    event["team_away_country"] = away
    event["team_tie_key"] = tie_key
    event["team_tie_label"] = tie_label
    event["team_round_label"] = round_label
    event["team_stage"] = stage
    event["stage"] = stage

    tz = _country_tz(home)
    if tz:
        event["tournament_timezone"] = tz
        event["tournament_cutoff_minutes"] = 360

    raw = event.setdefault("raw", {})
    raw["flashscore_round"] = stage
    return event


def apply_team_context(events: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = [decorate_event(event) for event in events]

    # For every national tie use one host timezone. Flashscore normally keeps
    # the host nation's player on the home side; taking the majority makes this
    # robust even if one rubber is displayed in the reverse order.
    groups: Dict[tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for event in rows:
        if not is_davis_cup_event(event):
            continue
        groups[(str(event.get("tournament_source_name") or ""), str(event.get("team_tie_key") or ""))].append(event)

    for group_rows in groups.values():
        home_codes = [str(row.get("team_home_country") or "") for row in group_rows]
        home_codes = [code for code in home_codes if _country_tz(code)]
        host = Counter(home_codes).most_common(1)[0][0] if home_codes else ""
        tz = _country_tz(host)
        if not tz:
            continue
        for row in group_rows:
            row["team_host_country"] = host
            row["tournament_timezone"] = tz
            row["tournament_cutoff_minutes"] = 360
    return rows


def install_provider() -> None:
    global _PROVIDER_INSTALLED
    if _PROVIDER_INSTALLED:
        return

    old_normalize_event = ss.normalize_event

    def normalize_event(raw_event):
        return decorate_event(old_normalize_event(raw_event))

    ss.normalize_event = normalize_event
    ss.is_davis_cup_event = is_davis_cup_event  # type: ignore[attr-defined]
    ss.apply_team_context = apply_team_context  # type: ignore[attr-defined]
    _PROVIDER_INSTALLED = True


def install_store() -> None:
    global _STORE_INSTALLED
    if _STORE_INSTALLED:
        return

    old_event_stage = store.event_stage

    def event_stage(event: Dict[str, Any], overrides: Dict[int, str]) -> str:
        decorate_event(event)
        if is_davis_cup_event(event):
            return str(event.get("team_stage") or event.get("team_round_label") or "Кубок Дэвиса")
        return old_event_stage(event, overrides)

    store.event_stage = event_stage

    def team_session_events(old_loader: Any, chat_id: int, day: dt.date, force_refresh: bool = False) -> List[Dict[str, Any]]:
        cache_key = (int(chat_id), day.isoformat())
        if not force_refresh and cache_key in store._CACHE and time.time() - store._CACHE[cache_key][0] < 45:  # type: ignore[attr-defined]
            return copy.deepcopy(store._CACHE[cache_key][1])  # type: ignore[attr-defined]

        by_id: Dict[int, Dict[str, Any]] = {}
        rank = {"finished": 5, "retired": 5, "walkover": 5, "cancelled": 4, "inprogress": 3, "notstarted": 2}
        for source_day in (day - dt.timedelta(days=1), day, day + dt.timedelta(days=1)):
            for row in old_loader(chat_id, source_day, force_refresh=force_refresh):
                event = copy.deepcopy(row)
                decorate_event(event)
                event["_source_day"] = source_day.isoformat()
                event_id = int(event["event_id"])
                old = by_id.get(event_id)
                if old is None or rank.get(ss.status_type(event), 1) > rank.get(ss.status_type(old), 1):
                    by_id[event_id] = event

        all_rows = apply_team_context(by_id.values())
        result: List[Dict[str, Any]] = []
        for event in all_rows:
            source = str(event.get("tournament_source_name") or event.get("tournament_name") or "Турнир")
            profile = store.get_profile(source)
            source_day = dt.date.fromisoformat(str(event.get("_source_day") or day.isoformat()))
            timestamp = int(event.get("start_ts") or 0)

            timezone = str(event.get("tournament_timezone") or profile.get("tz") or store.DEFAULT_TZ)
            try:
                cutoff = int(event.get("tournament_cutoff_minutes", profile.get("cutoff", store.DEFAULT_CUTOFF)))
            except Exception:
                cutoff = store.DEFAULT_CUTOFF

            local_day = source_day
            if timestamp:
                try:
                    local_day = (
                        dt.datetime.fromtimestamp(timestamp, ZoneInfo(timezone))
                        - dt.timedelta(minutes=cutoff)
                    ).date()
                except Exception:
                    local_day = source_day
            if local_day != day:
                continue

            # A team event keeps the source competition name; normal tournaments
            # still use the persistent profile rename.
            display_name = (
                str(event.get("tournament_name") or source)
                if is_davis_cup_event(event)
                else str(profile.get("name") or source)
            )
            event.update(
                {
                    "tournament_source_name": source,
                    "tournament_key": profile["key"],
                    "tournament_name": display_name,
                    "tournament_timezone": timezone,
                    "tournament_cutoff_minutes": cutoff,
                    "session_day": day.isoformat(),
                }
            )
            result.append(event)

        store.apply_stages(result)
        result.sort(
            key=lambda event: (
                int(event.get("tournament_sort_rank", 9)),
                str(event.get("tour_group")),
                str(event.get("tournament_name")).lower(),
                store.STAGE_ORDER.get(str(event.get("stage")), 90),
                str(event.get("team_tie_label") or ""),
                int(event.get("start_ts") or 0),
                int(event["event_id"]),
            )
        )
        store._CACHE[cache_key] = (time.time(), copy.deepcopy(result))  # type: ignore[attr-defined]
        return result

    store.session_events = team_session_events
    try:
        store._CACHE.clear()  # type: ignore[attr-defined]
    except Exception:
        pass
    _STORE_INSTALLED = True


def install_daily_summary(daily_summary: Any) -> None:
    global _SUMMARY_INSTALLED
    if _SUMMARY_INSTALLED:
        return

    old_is_doubles = daily_summary._is_doubles
    old_common_stage = daily_summary._common_stage

    def is_doubles(event: Dict[str, Any]) -> bool:
        # Davis Cup doubles are a scoring rubber of the team tie, not a separate
        # doubles tournament. They must be included in completion and summaries.
        if is_davis_cup_event(event):
            return False
        return old_is_doubles(event)

    def common_stage(events: List[Dict[str, Any]]) -> str:
        davis = [decorate_event(event) for event in events if is_davis_cup_event(event)]
        if davis:
            rounds = [str(event.get("team_round_label") or "") for event in davis]
            rounds = [value for value in rounds if value]
            if rounds:
                return Counter(rounds).most_common(1)[0][0]
            return "Кубок Дэвиса"
        return old_common_stage(events)

    daily_summary._is_doubles = is_doubles
    daily_summary._common_stage = common_stage
    _SUMMARY_INSTALLED = True


def install_validation_hooks() -> None:
    global _VALIDATION_INSTALLED
    if _VALIDATION_INSTALLED:
        return

    try:
        import tournament_sep6_validation_patch as validation

        old_filter = validation.is_adult_singles_event
        old_stage = validation._stage_from_match_page

        def event_filter(event: Dict[str, Any]) -> bool:
            if is_davis_cup_event(event):
                return True
            return old_filter(event)

        def stage_from_page(match: Dict[str, Any]) -> tuple[int, str]:
            decorate_event(match)
            if is_davis_cup_event(match):
                return int(match.get("event_id") or 0), str(match.get("team_stage") or _round_label(match))
            return old_stage(match)

        validation.is_adult_singles_event = event_filter
        validation._stage_from_match_page = stage_from_page
    except Exception as exc:
        print(f"[team-events] validation hook failed: {exc}")

    try:
        import tournament_stage_full_scan_patch as scan

        old_scan = scan._stage_from_page

        def scan_stage(match: Dict[str, Any]) -> tuple[int, str]:
            decorate_event(match)
            if is_davis_cup_event(match):
                return int(match.get("event_id") or 0), str(match.get("team_stage") or _round_label(match))
            return old_scan(match)

        scan._stage_from_page = scan_stage
    except Exception as exc:
        print(f"[team-events] stage scan hook failed: {exc}")

    _VALIDATION_INSTALLED = True
