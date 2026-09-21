from __future__ import annotations

import copy
from typing import Any

import tournament_session_store as store

_INSTALLED: set[str] = set()


def install_poll(module: Any) -> None:
    if "poll" in _INSTALLED:
        return
    store.install_round_capture()
    import daily_summary
    import gha_worker
    from auto_summary_complete_day_patch import install as install_auto_summary_guard
    from tournament_aug25_followup import install_common
    from tournament_sep2_fix import install_poll_safety, install_store_safety
    from tournament_sep6_validation_patch import install_poll as install_day_validation_poll
    from runtime_delivery_patch import install as install_delivery_resilience
    from team_tournament_patch import (
        install_daily_summary as install_team_summary,
        install_provider as install_team_provider,
        install_validation_hooks as install_team_validation,
    )

    install_delivery_resilience()
    install_team_provider()
    install_team_summary(daily_summary)
    install_team_validation()
    install_common()
    install_store_safety()
    install_poll_safety(gha_worker)
    install_auto_summary_guard(daily_summary)
    install_day_validation_poll(daily_summary)
    # gha_worker imported these functions directly, so refresh its bound reference.
    gha_worker.publish_daily_summaries = daily_summary.publish_daily_summaries
    old_send = gha_worker.send_match_result

    def send(token, chat, event, *args, **kwargs):
        context = store.WATCH_CONTEXT.get(int(event.get("event_id") or 0))
        if context:
            event = copy.deepcopy(event)
            event["tournament_name"] = context.get("tournament_name") or event.get("tournament_name")
            event["session_day"] = context.get("session_day") or event.get("session_day")
            if context.get("stage"):
                event["stage"] = context["stage"]
                event.setdefault("raw", {})["flashscore_round"] = context["stage"]
        return old_send(token, chat, event, *args, **kwargs)

    gha_worker.list_pending_match_watch_days = store.pending_source_days
    gha_worker.list_pending_match_watches = store.pending_watches
    gha_worker.mark_match_notified = lambda chat, day, event: store.mark_notified(chat, day, event)
    gha_worker.mark_event_notified = lambda day, event: store.mark_notified(None, day, event)
    gha_worker.send_match_result = send
    module.run_once = gha_worker.run_once
    _INSTALLED.add("poll")


def install_api_module(module: Any, route_name: str) -> Any:
    if route_name == "webhook" and "webhook" not in _INSTALLED:
        import daily_summary
        from tournament_session_webhook import install
        from tournament_session_ui_patch import install as install_ui_patch
        from tournament_stage_full_scan_patch import install as install_full_stage_scan
        from tournament_aug25_fix import install as install_aug25_fix
        from tournament_aug25_followup import install_common, install_webhook
        from compact_match_list_patch import install as install_compact_match_list
        from tournament_sep2_fix import install_store_safety, install_webhook as install_sep2_webhook
        from tournament_sep2_all_matches_patch import install as install_all_matches
        from player_alias_admin_patch import install as install_player_alias_admin
        from player_alias_search_v2_patch import install as install_player_alias_search_v2
        from tournament_sep6_validation_patch import install as install_day_validation
        from menu_history_patch import install as install_menu_history
        from known_major_backfill_patch import install as install_known_major_backfill
        from runtime_delivery_patch import install as install_delivery_resilience
        from team_tournament_patch import (
            install_daily_summary as install_team_summary,
            install_provider as install_team_provider,
            install_store as install_team_store,
            install_validation_hooks as install_team_validation,
        )

        install_delivery_resilience()
        # Team-event normalization must be installed before the session loader is
        # wrapped, otherwise Davis Cup events are first filtered with one global
        # tournament timezone.
        install_team_provider()
        install_team_summary(daily_summary)
        install_team_store()
        install_common()
        install_store_safety()
        install(module)
        install_ui_patch(module)
        install_full_stage_scan(module)
        install_aug25_fix(module)
        install_webhook(module)
        install_compact_match_list(module)
        install_sep2_webhook(module)
        install_all_matches(module)
        install_player_alias_admin(module)
        install_player_alias_search_v2(module)
        # Must run after older session/menu wrappers: validate the final event stack,
        # add robust historical source recovery, then apply a last-resort known-major
        # backfill so a completed major final cannot disappear from the menu.
        install_day_validation(module)
        install_team_validation()
        install_menu_history(module)
        install_known_major_backfill(module)
        _INSTALLED.add("webhook")
    elif route_name == "poll":
        install_poll(module)
    return module
