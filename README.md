# Tennis Scores Bot — Vercel + GitHub Actions (free)

- Vercel (serverless FastAPI) hosts `/api/webhook` for Telegram.
- GitHub Actions runs `gha_worker.py` on a schedule (every 5 min) and sends results.
- Postgres (Neon/Supabase) stores users/watchlist/notified.

See `.env.example` and `.github/workflows/tennis-poller.yml`.
