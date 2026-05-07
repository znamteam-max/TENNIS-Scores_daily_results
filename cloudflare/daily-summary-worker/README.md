# Tennis Daily Summary Worker

Cloudflare Worker for publishing end-of-day tennis summaries with odds-based categories.

The existing Vercel bot still renders PNG result cards and refreshes `events_cache` in Neon. This Worker reads that cache, stores match odds, builds the daily summary text, and sends it to Telegram.

## Deploy

```bash
cd cloudflare/daily-summary-worker
npm install
npx wrangler login
npx wrangler secret put DATABASE_URL
npx wrangler secret put TELEGRAM_BOT_TOKEN
npx wrangler secret put PUBLISH_CHAT_ID
npx wrangler secret put ODDS_API_KEY
npx wrangler secret put CRON_SECRET
npx wrangler deploy
```

Optional secrets/vars:

- `SUMMARY_CHAT_ID` overrides `PUBLISH_CHAT_ID`.
- `ODDS_API_SPORT_KEYS` fixes The Odds API sport keys, comma-separated.
- `ODDS_API_BOOKMAKERS` limits odds to specific bookmakers.
- `SUMMARY_TOURNAMENT_ALLOWLIST` / `SUMMARY_TOURNAMENT_BLOCKLIST` filter tournaments by name.
- `SUMMARY_RUSSIAN_NAME_HINTS` adds Russian-player surname hints.

Manual run:

```bash
curl "https://<worker>.workers.dev/run?day=2026-05-07&secret=<CRON_SECRET>"
```

After Cloudflare is live, set `SUMMARY_ENABLED=0` on Vercel to avoid two schedulers trying to publish the same daily summary.
