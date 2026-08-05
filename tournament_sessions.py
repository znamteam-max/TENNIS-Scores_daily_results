from __future__ import annotations

import copy
from typing import Any

import tournament_session_store as store

_INSTALLED: set[str] = set()


def install_poll(module: Any) -> None:
    if "poll" in _INSTALLED:
        return
    store.install_round_capture()
    import gha_worker

    old_send = gha_worker.send_match_result

    def send(token, chat, event, *args, **kwargs):
        context = store.WATCH_CONTEXT.get(int(event.get("event_id") or 0))
        if context:
            event = copy.deepcopy(event)
            event["tournament_name"] = context.get("tournament_name") or event.get("tournament_name")
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
        from tournament_session_webhook import install
        from tournament_session_ui_patch import install as install_ui_patch
        from tournament_stage_full_scan_patch import install as install_full_stage_scan

        install(module)
        install_ui_patch(module)
        install_full_stage_scan(module)
        _INSTALLED.add("webhook")
    elif route_name == "poll":
        install_poll(module)
    return module
