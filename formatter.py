from typing import Dict, Any, List, Optional

# Build a Russian message in the format requested.
def build_match_message(event: Dict[str, Any]) -> str:
    # 'event' is a normalized dict produced by the provider with the keys:
    # {
    #   'event_id': str,
    #   'home_name': str, 'away_name': str,
    #   'score_sets': ['7:5', '3:6', '7:5'],
    #   'duration': '2:48' or None,
    #   'home_stats': {...}, 'away_stats': {...}
    # }
    title = f"{event['home_name']} — {event['away_name']}\n"
    score_line = "Счёт: " + ", ".join(event.get('score_sets') or []) + "\n"
    duration_line = "Время: " + (event.get('duration') or 'н/д') + "\n\n"

    def stats_block(name: str, s: Dict[str, Any]) -> str:
        # s keys: aces, doubles, first_serve_in_pct, first_serve_points_won_pct, second_serve_points_won_pct,
        # winners, unforced, break_points_saved, break_points_faced, match_points_saved
        # Note: Some fields may be None.
        lines = [name, ""]
        def fmt_pct(v):
            return f"{int(round(v))}%" if isinstance(v, (int,float)) else "н/д"
        def fmt_int(v):
            return str(v) if isinstance(v, (int, float)) else "н/д"

        lines.append(f"Эйсы: {fmt_int(s.get('aces'))}")
        lines.append(f"Двойные: {fmt_int(s.get('doubles'))}")
        lines.append(f"% попадания первой подачи: {fmt_pct(s.get('first_serve_in_pct'))}")
        lines.append(f"Очки выигр. на п.п.: {fmt_pct(s.get('first_serve_points_won_pct'))}")
        lines.append(f"Очки выигр. на в.п.: {fmt_pct(s.get('second_serve_points_won_pct'))}")
        lines.append(f"Виннеры: {fmt_int(s.get('winners'))}")
        lines.append(f"Невынужденные: {fmt_int(s.get('unforced'))}")
        bps = s.get('break_points_saved')
        bpf = s.get('break_points_faced')
        if bps is not None and bpf is not None:
            lines.append(f"Спасенные б.п.: {int(bps)}/{int(bpf)}")
        else:
            lines.append("Спасенные б.п.: н/д")
        mps = s.get('match_points_saved')
        lines.append(f"Спасенные м.б.: {fmt_int(mps) if mps is not None else 'н/д'}")
        return "\n".join(lines)

    home_block = stats_block(event['home_name'], event.get('home_stats') or {})
    away_block = stats_block(event['away_name'], event.get('away_stats') or {})

    return title + score_line + duration_line + home_block + "\n\n" + away_block
