# Tennis Scores Bot — Vercel + GitHub Actions (free)

- Vercel (serverless FastAPI) hosts `/api/webhook` for Telegram.
- GitHub Actions runs `gha_worker.py` on a schedule (every 5 min) and sends results.
- Postgres (Neon/Supabase) stores users/watchlist/notified.

See `.env.example` and `.github/workflows/tennis-poller.yml`.

## Telegram live overlay menu

The bot command `/overlay` opens a live production menu:

1. choose a live Flashscore tennis match;
2. choose OBS, Streamlabs, or vMix;
3. choose `stats` or `chat` overlay mode;
4. receive a ready Browser Source/Web Browser URL with setup steps.

The default overlay host is:

```text
https://tennis-listen-bolshe-overlay.znamteam-903.workers.dev
```

If the overlay Worker changes, set `OVERLAY_PUBLIC_BASE_URL` in Vercel.
