# Tennis Scores Bot — Vercel + GitHub Actions (free)

- Vercel (serverless FastAPI) hosts `/api/webhook` for Telegram.
- GitHub Actions runs `gha_worker.py` on a schedule (every 5 min) and sends results.
- Postgres (Neon/Supabase) stores users/watchlist/notified.

See `.env.example` and `.github/workflows/tennis-poller.yml`.

## Cloudflare live overlay

The Cloudflare Worker in `cloudflare/daily-summary-worker` also exposes a lightweight live tennis overlay for OBS, Streamlabs and vMix.

Routes:

- `/overlay.html`
- `/overlay.css`
- `/overlay.js`
- `/api/match/flashscore?id=Sril3X2m`
- `/api/news/tennis`
- `/api/matches`

Example Browser Source URL:

```text
https://tennis-daily-results.znamteam-903.workers.dev/overlay.html?source=/api/match/flashscore?id=Sril3X2m&news=/api/news/tennis&panel=stats&poll=3000
```

Use `panel=chat` to switch the left panel to chat mode. Add `guides=1` for layout guides while setting up a scene.
