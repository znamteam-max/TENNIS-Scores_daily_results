import { neon } from "@neondatabase/serverless";

const TARGET_RANKS = new Set([0, 1, 2, 3]);
const TARGET_CATEGORIES = new Set(["ATP", "WTA"]);

const GRAND_SLAM_TOURNAMENTS = [
  "australian open",
  "open australia",
  "\u043e\u0442\u043a\u0440\u044b\u0442\u044b\u0439 \u0447\u0435\u043c\u043f\u0438\u043e\u043d\u0430\u0442 \u0430\u0432\u0441\u0442\u0440\u0430\u043b\u0438\u0438",
  "\u0430\u0432\u0441\u0442\u0440\u0430\u043b\u0438\u044f open",
  "roland garros",
  "french open",
  "\u0440\u043e\u043b\u0430\u043d \u0433\u0430\u0440\u0440\u043e\u0441",
  "wimbledon",
  "\u0443\u0438\u043c\u0431\u043b\u0434\u043e\u043d",
  "us open",
  "\u043e\u0442\u043a\u0440\u044b\u0442\u044b\u0439 \u0447\u0435\u043c\u043f\u0438\u043e\u043d\u0430\u0442 \u0441\u0448\u0430"
];

const COMMON_1000_TOURNAMENTS = [
  "indian wells",
  "\u0438\u043d\u0434\u0438\u0430\u043d-\u0443\u044d\u043b\u043b\u0441",
  "\u0438\u043d\u0434\u0438\u0430\u043d \u0443\u044d\u043b\u043b\u0441",
  "miami",
  "\u043c\u0430\u0439\u0430\u043c\u0438",
  "madrid",
  "\u043c\u0430\u0434\u0440\u0438\u0434",
  "rome",
  "\u0440\u0438\u043c",
  "canada",
  "canadian open",
  "toronto",
  "\u0442\u043e\u0440\u043e\u043d\u0442\u043e",
  "montreal",
  "\u043c\u043e\u043d\u0440\u0435\u0430\u043b\u044c",
  "cincinnati",
  "\u0446\u0438\u043d\u0446\u0438\u043d\u043d\u0430\u0442\u0438"
];

const ATP_1000_TOURNAMENTS = [
  "monte carlo",
  "\u043c\u043e\u043d\u0442\u0435-\u043a\u0430\u0440\u043b\u043e",
  "\u043c\u043e\u043d\u0442\u0435 \u043a\u0430\u0440\u043b\u043e",
  "shanghai",
  "\u0448\u0430\u043d\u0445\u0430\u0439",
  "paris",
  "\u043f\u0430\u0440\u0438\u0436"
];

const WTA_1000_TOURNAMENTS = [
  "doha",
  "\u0434\u043e\u0445\u0430",
  "dubai",
  "\u0434\u0443\u0431\u0430\u0439",
  "beijing",
  "\u043f\u0435\u043a\u0438\u043d",
  "wuhan",
  "\u0443\u0445\u0430\u043d\u044c"
];

const ATP_500_TOURNAMENTS = [
  "rotterdam",
  "\u0440\u043e\u0442\u0442\u0435\u0440\u0434\u0430\u043c",
  "doha",
  "\u0434\u043e\u0445\u0430",
  "dubai",
  "\u0434\u0443\u0431\u0430\u0439",
  "rio de janeiro",
  "\u0440\u0438\u043e-\u0434\u0435-\u0436\u0430\u043d\u0435\u0439\u0440\u043e",
  "acapulco",
  "\u0430\u043a\u0430\u043f\u0443\u043b\u044c\u043a\u043e",
  "barcelona",
  "\u0431\u0430\u0440\u0441\u0435\u043b\u043e\u043d\u0430",
  "queens",
  "queen's",
  "\u043b\u043e\u043d\u0434\u043e\u043d",
  "halle",
  "\u0445\u0430\u043b\u043b\u0435",
  "washington",
  "\u0432\u0430\u0448\u0438\u043d\u0433\u0442\u043e\u043d",
  "beijing",
  "\u043f\u0435\u043a\u0438\u043d",
  "tokyo",
  "\u0442\u043e\u043a\u0438\u043e",
  "basel",
  "\u0431\u0430\u0437\u0435\u043b\u044c",
  "vienna",
  "\u0432\u0435\u043d\u0430",
  "hamburg",
  "\u0433\u0430\u043c\u0431\u0443\u0440\u0433",
  "dallas",
  "\u0434\u0430\u043b\u043b\u0430\u0441"
];

const WTA_500_TOURNAMENTS = [
  "brisbane",
  "\u0431\u0440\u0438\u0441\u0431\u0435\u043d",
  "adelaide",
  "\u0430\u0434\u0435\u043b\u0430\u0438\u0434\u0430",
  "abu dhabi",
  "\u0430\u0431\u0443-\u0434\u0430\u0431\u0438",
  "linz",
  "\u043b\u0438\u043d\u0446",
  "stuttgart",
  "\u0448\u0442\u0443\u0442\u0433\u0430\u0440\u0442",
  "charleston",
  "\u0447\u0430\u0440\u043b\u044c\u0441\u0442\u043e\u043d",
  "strasbourg",
  "\u0441\u0442\u0440\u0430\u0441\u0431\u0443\u0440\u0433",
  "berlin",
  "\u0431\u0435\u0440\u043b\u0438\u043d",
  "bad homburg",
  "\u0431\u0430\u0434-\u0445\u043e\u043c\u0431\u0443\u0440\u0433",
  "eastbourne",
  "\u0438\u0441\u0442\u0431\u043e\u0440\u043d",
  "seoul",
  "\u0441\u0435\u0443\u043b",
  "ningbo",
  "\u043d\u0438\u043d\u0431\u043e",
  "tokyo",
  "\u0442\u043e\u043a\u0438\u043e"
];

const RUSSIAN_NAME_HINTS = [
  "\u043c\u0435\u0434\u0432\u0435\u0434\u0435\u0432",
  "\u0440\u0443\u0431\u043b\u0435\u0432",
  "\u0440\u0443\u0431\u043b\u0451\u0432",
  "\u0445\u0430\u0447\u0430\u043d\u043e\u0432",
  "\u0441\u0430\u0444\u0438\u0443\u043b\u043b\u0438\u043d",
  "\u043a\u0430\u0440\u0430\u0446\u0446\u0435\u0432",
  "\u043a\u0430\u0440\u0430\u0442\u0446\u0435\u0432",
  "\u043a\u043e\u0442\u043e\u0432",
  "\u0430\u043d\u0434\u0440\u0435\u0435\u0432",
  "\u0430\u043d\u0434\u0440\u0435\u0435\u0432a",
  "\u0430\u043d\u0434\u0440\u0435\u0435\u0432\u0430",
  "\u0448\u043d\u0430\u0439\u0434\u0435\u0440",
  "\u0430\u043b\u0435\u043a\u0441\u0430\u043d\u0434\u0440\u043e\u0432\u0430",
  "\u043a\u0430\u0441\u0430\u0442\u043a\u0438\u043d\u0430",
  "\u0441\u0430\u043c\u0441\u043e\u043d\u043e\u0432\u0430",
  "\u043a\u0443\u0434\u0435\u0440\u043c\u0435\u0442\u043e\u0432\u0430",
  "\u043f\u043e\u0442\u0430\u043f\u043e\u0432\u0430",
  "\u043f\u0430\u0432\u043b\u044e\u0447\u0435\u043d\u043a\u043e\u0432\u0430",
  "\u043a\u0430\u043b\u0438\u043d\u0441\u043a\u0430\u044f",
  "\u0431\u043b\u0438\u043d\u043a\u043e\u0432\u0430",
  "\u0440\u0430\u0445\u0438\u043c\u043e\u0432\u0430",
  "\u0430\u0432\u0430\u043d\u0435\u0441\u0441\u044f\u043d",
  "\u0437\u0432\u043e\u043d\u0430\u0440\u0435\u0432\u0430",
  "\u0437\u0432\u043e\u043d\u0430\u0440\u0451\u0432\u0430",
  "medvedev",
  "rublev",
  "khachanov",
  "safiullin",
  "karatsev",
  "kotov",
  "andreev",
  "andreeva",
  "shnaider",
  "alexandrova",
  "kasatkina",
  "samsonova",
  "kudermetova",
  "potapova",
  "pavlyuchenkova",
  "kalinskaya",
  "blinkova",
  "rakhimova",
  "avanesyan",
  "zvonareva"
];

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/health") {
      return json({ ok: true, service: "tennis-daily-summary-worker" });
    }
    if (url.pathname === "/diag") {
      return json({
        ok: true,
        service: "tennis-daily-summary-worker",
        env: envShape(env)
      });
    }
    if (url.pathname === "/run") {
      if (!isAuthorized(request, url, env)) {
        return json({ ok: false, error: "unauthorized" }, 401);
      }
      try {
        const day = url.searchParams.get("day") || "";
        const forceOdds = ["1", "true", "yes", "on"].includes(String(url.searchParams.get("forceOdds") || url.searchParams.get("force") || "").toLowerCase());
        const debug = ["1", "true", "yes", "on"].includes(String(url.searchParams.get("debug") || "").toLowerCase());
        const result = await runDailySummary(env, {
          ...(day ? { days: [day] } : {}),
          forceOdds,
          debug
        });
        return json({ ok: true, ...result });
      } catch (error) {
        console.log(`[run] failed: ${error?.stack || error?.message || error}`);
        return json({ ok: false, error: error?.message || String(error) }, 500);
      }
    }
    return json({
      ok: true,
      service: "tennis-daily-summary-worker",
      routes: ["/health", "/diag", "/run?day=YYYY-MM-DD&secret=CRON_SECRET", "/run?day=YYYY-MM-DD&forceOdds=1&debug=1&secret=CRON_SECRET"]
    });
  },

  async scheduled(_controller, env, ctx) {
    ctx.waitUntil(runDailySummary(env));
  }
};

async function runDailySummary(env, options = {}) {
  const sql = db(env);
  await ensureSchema(sql);
  const today = localDate(env.APP_TZ || "Europe/Helsinki");
  const days = options.days || [today, addDays(today, -1)];
  const out = [];
  for (const day of days) {
    const data = await getEventsCache(sql, day);
    if (!data) {
      out.push({ day, cached: false, oddsSaved: 0, summariesSent: 0 });
      continue;
    }
    const events = normalizeEvents(data);
    const targetEvents = events.filter(isTargetEvent);
    const oddsSaved = await cacheMatchOdds(sql, env, day, targetEvents, options);
    const summariesSent = await publishDailySummaries(sql, env, day, events);
    out.push({
      day,
      cached: true,
      events: events.length,
      targetEvents: targetEvents.length,
      oddsSaved,
      summariesSent,
      ...(options.debug ? { debug: await debugDay(sql, day, events, targetEvents) } : {})
    });
  }
  return { days: out };
}

async function debugDay(sql, day, events, targetEvents) {
  const oddsMap = await getOddsMap(sql, day);
  const statusCounts = countBy(targetEvents, (event) => String(event.status_type || event.raw?.status?.type || "unknown").toLowerCase());
  const sourceCounts = countBy(targetEvents, (event) => String(event.raw?.source || "unknown").toLowerCase());
  const finished = targetEvents.filter(isFinished);
  const withResultLine = finished.filter((event) => resultLine(event));
  const withFlashscoreId = withResultLine.filter((event) => flashscoreMatchId(event));
  const missingOdds = withFlashscoreId.filter((event) => !oddsMap.has(event.event_id));
  const groups = await Promise.all(targetGroups(events).map(async (item) => {
    const { group, tournament, status, rows } = item;
    const stage = commonStage(rows);
    const key = summaryKey(day, group, tournament, status, stage);
    const lineRows = rows.filter((event) => resultLine(event));
    const usableOddsRows = rows.filter((event) => {
      const odds = oddsMap.get(event.event_id);
      return odds?.home_odds && odds?.away_odds;
    });
    return {
      group,
      tournament,
      status,
      stage,
      rows: rows.length,
      finishedRows: rows.filter(isFinished).length,
      allFinished: rows.length > 0 && rows.every(isFinished),
      resultLineRows: lineRows.length,
      oddsRows: rows.filter((event) => oddsMap.has(event.event_id)).length,
      usableOddsRows: usableOddsRows.length,
      alreadySent: await isDailySummarySent(sql, key)
    };
  }));
  return {
    events: events.length,
    targetEvents: targetEvents.length,
    statusCounts,
    sourceCounts,
    finishedTargetEvents: finished.length,
    withResultLine: withResultLine.length,
    withFlashscoreId: withFlashscoreId.length,
    oddsRows: oddsMap.size,
    usableOddsRows: [...oddsMap.values()].filter((row) => row?.home_odds && row?.away_odds).length,
    missingFlashscoreOdds: missingOdds.length,
    groups,
    samples: targetEvents.slice(0, 12).map((event) => ({
      event_id: event.event_id,
      custom_id: event.custom_id || "",
      raw_source: event.raw?.source || "",
      raw_flashscore_id: event.raw?.flashscore_id || "",
      status_type: event.status_type || "",
      home_name: event.home_name || "",
      away_name: event.away_name || "",
      has_result_line: Boolean(resultLine(event)),
      flashscore_match_id: flashscoreMatchId(event)
    }))
  };
}

function countBy(items, keyFn) {
  const out = {};
  for (const item of items) {
    const key = keyFn(item) || "unknown";
    out[key] = (out[key] || 0) + 1;
  }
  return out;
}

function db(env) {
  const url = env.DATABASE_URL || env.POSTGRES_URL;
  if (!url) {
    throw new Error("DATABASE_URL is not set");
  }
  return neon(url);
}

async function ensureSchema(sql) {
  await sql`
    create table if not exists match_odds (
      event_id bigint primary key,
      day date not null,
      home_odds double precision,
      away_odds double precision,
      source text not null default 'unknown',
      raw jsonb not null default '{}'::jsonb,
      fetched_at timestamptz not null default now()
    )
  `;
  await sql`
    create table if not exists odds_refreshes (
      day date primary key,
      refreshed_at timestamptz not null default now()
    )
  `;
  await sql`
    create table if not exists daily_summaries (
      summary_key text primary key,
      day date not null,
      tour_group text not null,
      tournament_name text not null,
      tournament_status text not null,
      stage text not null default '',
      sent_at timestamptz not null default now()
    )
  `;
}

async function getEventsCache(sql, day) {
  const rows = await sql`select data from events_cache where ds = ${day} limit 1`;
  return rows[0]?.data || null;
}

async function oddsRefreshDue(sql, day, refreshMinutes) {
  const rows = await sql`select refreshed_at from odds_refreshes where day = ${day} limit 1`;
  if (!rows.length) {
    return true;
  }
  const refreshedAt = new Date(rows[0].refreshed_at).getTime();
  return Date.now() - refreshedAt > refreshMinutes * 60 * 1000;
}

async function markOddsRefresh(sql, day) {
  await sql`
    insert into odds_refreshes (day, refreshed_at)
    values (${day}, now())
    on conflict (day) do update set refreshed_at = now()
  `;
}

async function cacheMatchOdds(sql, env, day, targetEvents, options = {}) {
  const source = String(env.SUMMARY_ODDS_SOURCE || "flashscore").toLowerCase().replace(/-/g, "_");
  if (["0", "false", "no", "off", "none"].includes(source)) {
    return 0;
  }
  if (source === "odds_api") {
    return cacheMatchOddsFromOddsApi(sql, env, day, targetEvents, options);
  }
  return cacheMatchOddsFromFlashscore(sql, env, day, targetEvents, options);
}

async function cacheMatchOddsFromFlashscore(sql, env, day, targetEvents, options = {}) {
  if (isOff(env.FLASHSCORE_ODDS_ENABLED, "1")) {
    return 0;
  }

  const oddsMap = await getOddsMap(sql, day);
  const allEventsToFetch = targetEvents
    .filter((event) => isFinished(event))
    .filter((event) => resultLine(event))
    .filter((event) => flashscoreMatchId(event))
    .filter((event) => !oddsMap.has(event.event_id));

  if (!allEventsToFetch.length) {
    return 0;
  }

  const refreshMinutes = Number(env.FLASHSCORE_ODDS_REFRESH_MINUTES || env.ODDS_REFRESH_MINUTES || 30);
  if (!options.forceOdds && !(await oddsRefreshDue(sql, day, refreshMinutes))) {
    return 0;
  }

  const fetchLimit = Math.max(1, Number(env.FLASHSCORE_ODDS_FETCH_LIMIT || 8));
  const eventsToFetch = allEventsToFetch.slice(0, fetchLimit);
  const found = await flashscoreOddsForEvents(env, eventsToFetch);
  let saved = 0;
  for (const event of eventsToFetch) {
    const odds = found.get(event.event_id);
    if (!odds?.home_odds || !odds?.away_odds) {
      continue;
    }
    await upsertMatchOdds(sql, event.event_id, day, odds.home_odds, odds.away_odds, "flashscore", odds.raw);
    saved += 1;
  }

  if (allEventsToFetch.length <= fetchLimit) {
    await markOddsRefresh(sql, day);
  }
  console.log(`[summary] flashscore odds cached day=${day} saved=${saved} checked=${eventsToFetch.length} remaining=${Math.max(0, allEventsToFetch.length - eventsToFetch.length)}`);
  return saved;
}

async function cacheMatchOddsFromOddsApi(sql, env, day, targetEvents, _options = {}) {
  if (!env.ODDS_API_KEY) {
    return 0;
  }
  const refreshMinutes = Number(env.ODDS_REFRESH_MINUTES || 30);
  if (!(await oddsRefreshDue(sql, day, refreshMinutes))) {
    return 0;
  }
  const eventsToMatch = targetEvents.filter((event) => !isFinished(event));
  if (!eventsToMatch.length) {
    await markOddsRefresh(sql, day);
    return 0;
  }
  const oddsItems = await oddsByDate(env, day);
  let saved = 0;
  for (const event of eventsToMatch) {
    const item = matchOddsItem(event, oddsItems);
    if (!item) {
      continue;
    }
    const [homeOdds, awayOdds] = oddsPricesForEvent(event, item);
    if (!homeOdds || !awayOdds) {
      continue;
    }
    await upsertMatchOdds(sql, event.event_id, day, homeOdds, awayOdds, item.sport_key || "the-odds-api", item);
    saved += 1;
  }
  await markOddsRefresh(sql, day);
  console.log(`[summary] odds api cached day=${day} saved=${saved} source_events=${oddsItems.length}`);
  return saved;
}

async function flashscoreOddsForEvents(env, events) {
  const out = new Map();
  const limit = Math.max(1, Number(env.FLASHSCORE_ODDS_CONCURRENCY || 4));
  let index = 0;
  async function worker() {
    for (;;) {
      const event = events[index++];
      if (!event) {
        break;
      }
      try {
        const odds = await flashscoreOddsForEvent(env, event);
        if (odds) {
          out.set(event.event_id, odds);
        }
      } catch (error) {
        console.log(`[flashscore_odds] fetch failed event_id=${event.event_id}: ${error?.message || error}`);
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, events.length) }, worker));
  return out;
}

async function flashscoreOddsForEvent(env, event) {
  const matchId = flashscoreMatchId(event);
  if (!matchId) {
    return null;
  }

  const base = String(env.FLASHSCORE_BASE || "https://www.flashscorekz.com").replace(/\/+$/, "");
  const pageUrl = `${base}/match/${matchId}/#/odds-comparison/1x2-odds/full-time`;
  const pageResponse = await fetch(pageUrl, {
    headers: {
      accept: "text/html,*/*",
      "accept-language": "ru-RU,ru;q=0.9,en-US;q=0.5,en;q=0.4",
      "user-agent": flashscoreUserAgent()
    }
  });
  if (!pageResponse.ok) {
    throw new Error(`page ${pageResponse.status}`);
  }
  const page = await pageResponse.text();
  const { homeEp, awayEp, projectId } = flashscorePageMeta(page);

  const oddsUrl = new URL(env.FLASHSCORE_ODDS_URL || "https://global.ds.lsapp.eu/odds/pq_graphql");
  oddsUrl.searchParams.set("_hash", "oce");
  oddsUrl.searchParams.set("eventId", matchId);
  oddsUrl.searchParams.set("projectId", String(env.FLASHSCORE_PROJECT_ID || projectId || 46));
  oddsUrl.searchParams.set("geoIpCode", String(env.FLASHSCORE_GEOIP_CODE || ""));
  oddsUrl.searchParams.set("geoIpSubdivisionCode", String(env.FLASHSCORE_GEOIP_SUBDIVISION_CODE || ""));

  const response = await fetch(oddsUrl.toString(), {
    headers: {
      accept: "application/json,*/*",
      "accept-language": "ru-RU,ru;q=0.9,en-US;q=0.5,en;q=0.4",
      origin: base,
      referer: pageUrl,
      "user-agent": flashscoreUserAgent()
    }
  });
  if (!response.ok) {
    throw new Error(`odds ${response.status} ${await response.text()}`);
  }

  const data = await response.json();
  const root = data?.data?.findOddsByEventId;
  if (!root) {
    return null;
  }
  const pairs = flashscoreFullTimeHomeAwayPairs(root, homeEp, awayEp);
  const activePairs = pairs.filter((pair) => pair.active);
  const selected = activePairs.length ? activePairs : pairs;
  if (!selected.length) {
    return null;
  }

  return {
    home_odds: median(selected.map((pair) => pair.home_odds)),
    away_odds: median(selected.map((pair) => pair.away_odds)),
    raw: {
      match_id: matchId,
      home_event_participant_id: homeEp,
      away_event_participant_id: awayEp,
      pairs: selected,
      pairs_count: pairs.length
    }
  };
}

function flashscoreMatchId(event) {
  const raw = event.raw || {};
  if (raw.source && raw.source !== "flashscore") {
    return "";
  }
  return String(raw.flashscore_id || raw.customId || event.custom_id || "").trim();
}

function flashscorePageMeta(page) {
  const text = String(page || "");
  const participantsMatch = text.match(/"participantsData":(\{.*?\}),"eventParticipantEncodedId"/s);
  let homeEp = "";
  let awayEp = "";
  if (participantsMatch) {
    try {
      const participants = JSON.parse(participantsMatch[1]);
      homeEp = String(participants.home?.[0]?.eventParticipantId || "");
      awayEp = String(participants.away?.[0]?.eventParticipantId || "");
    } catch (_error) {
    }
  }
  const projectMatch = text.match(/"project":\{"id":(\d+)/);
  return { homeEp, awayEp, projectId: projectMatch ? Number(projectMatch[1]) : 46 };
}

function flashscoreFullTimeHomeAwayPairs(root, homeEp, awayEp) {
  const bookmakerNames = new Map();
  for (const item of root.settings?.bookmakers || []) {
    const id = Number(item.bookmaker?.id);
    if (Number.isFinite(id)) {
      bookmakerNames.set(id, String(item.bookmaker?.name || id));
    }
  }

  const pairs = [];
  for (const market of root.odds || []) {
    if (market.bettingType !== "HOME_AWAY" || market.bettingScope !== "FULL_TIME") {
      continue;
    }

    const items = market.odds || [];
    const byParticipant = new Map(items.map((item) => [String(item.eventParticipantId || ""), item]));
    let homeItem = homeEp ? byParticipant.get(homeEp) : null;
    let awayItem = awayEp ? byParticipant.get(awayEp) : null;
    if ((!homeItem || !awayItem) && items.length === 2) {
      [homeItem, awayItem] = items;
    }

    const homeOdds = Number(String(homeItem?.value || "").replace(",", "."));
    const awayOdds = Number(String(awayItem?.value || "").replace(",", "."));
    if (!Number.isFinite(homeOdds) || !Number.isFinite(awayOdds) || homeOdds <= 1 || awayOdds <= 1) {
      continue;
    }

    const bookmakerId = Number(market.bookmakerId) || 0;
    pairs.push({
      bookmaker_id: bookmakerId,
      bookmaker_name: bookmakerNames.get(bookmakerId) || String(bookmakerId || "unknown"),
      home_odds: homeOdds,
      away_odds: awayOdds,
      active: Boolean(homeItem?.active) && Boolean(awayItem?.active)
    });
  }
  return pairs;
}

function isOff(value, defaultValue = "") {
  return ["0", "false", "no", "off"].includes(String(value ?? defaultValue).toLowerCase());
}

function flashscoreUserAgent() {
  const agents = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"
  ];
  return agents[Math.floor(Math.random() * agents.length)];
}

async function oddsByDate(env, day) {
  const keys = await tennisSportKeys(env);
  const [from, to] = dayWindowUtc(day, env.APP_TZ || "Europe/Helsinki");
  const out = [];
  for (const key of keys) {
    const params = {
      apiKey: env.ODDS_API_KEY,
      markets: env.ODDS_API_MARKETS || "h2h",
      oddsFormat: "decimal",
      dateFormat: "iso",
      commenceTimeFrom: from,
      commenceTimeTo: to
    };
    if (env.ODDS_API_BOOKMAKERS) {
      params.bookmakers = env.ODDS_API_BOOKMAKERS;
    } else {
      params.regions = env.ODDS_API_REGIONS || "eu";
    }
    try {
      const data = await getOddsJson(`/v4/sports/${key}/odds/`, params);
      for (const item of data || []) {
        item.sport_key = item.sport_key || key;
        out.push(item);
      }
    } catch (error) {
      console.log(`[odds] fetch failed sport=${key}: ${error?.message || error}`);
    }
  }
  return out;
}

async function tennisSportKeys(env) {
  const configured = splitList(env.ODDS_API_SPORT_KEYS);
  if (configured.length) {
    return configured;
  }
  const sports = await getOddsJson("/v4/sports/", { apiKey: env.ODDS_API_KEY, all: "true" });
  return (sports || [])
    .filter((sport) => String(sport.group || "").toLowerCase() === "tennis")
    .filter((sport) => !sport.has_outrights)
    .map((sport) => String(sport.key || "").trim())
    .filter((key) => key.startsWith("tennis_"));
}

async function getOddsJson(path, params) {
  const url = new URL(`https://api.the-odds-api.com${path}`);
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") {
      url.searchParams.set(key, value);
    }
  }
  const response = await fetch(url.toString());
  if (!response.ok) {
    throw new Error(`${response.status} ${await response.text()}`);
  }
  return response.json();
}

async function upsertMatchOdds(sql, eventId, day, homeOdds, awayOdds, source, raw) {
  await sql`
    insert into match_odds (event_id, day, home_odds, away_odds, source, raw, fetched_at)
    values (${eventId}, ${day}, ${homeOdds}, ${awayOdds}, ${source}, ${JSON.stringify(raw)}::jsonb, now())
    on conflict (event_id) do update
    set day = excluded.day,
        home_odds = excluded.home_odds,
        away_odds = excluded.away_odds,
        source = excluded.source,
        raw = excluded.raw,
        fetched_at = now()
  `;
}

async function getOddsMap(sql, day) {
  const rows = await sql`
    select event_id, home_odds, away_odds, source, raw, fetched_at
    from match_odds
    where day = ${day}
  `;
  const out = new Map();
  for (const row of rows) {
    out.set(Number(row.event_id), row);
  }
  return out;
}

async function publishDailySummaries(sql, env, day, events) {
  if (isOff(env.SUMMARY_ENABLED, "1")) {
    return 0;
  }
  const token = env.TELEGRAM_BOT_TOKEN;
  const chatId = env.SUMMARY_CHAT_ID || env.PUBLISH_CHAT_ID || env.RESULTS_CHAT_ID;
  if (!token || !chatId) {
    return 0;
  }

  const oddsMap = await getOddsMap(sql, day);
  const groups = targetGroups(events);
  const isPastDay = day < localDate(env.APP_TZ || "Europe/Helsinki");
  let sent = 0;
  for (const item of groups) {
    const { group, tournament, status, rows } = item;
    const resultRows = rows.filter((event) => resultLine(event));
    if (!resultRows.length) {
      continue;
    }
    if (!isPastDay && !rows.every(isFinished)) {
      continue;
    }
    if (requiresOdds(env) && resultRows.some((event) => {
      const odds = oddsMap.get(event.event_id);
      return !odds?.home_odds || !odds?.away_odds;
    })) {
      continue;
    }

    const stage = commonStage(rows);
    const key = summaryKey(day, group, tournament, status, stage);
    if (await isDailySummarySent(sql, key)) {
      continue;
    }

    const text = buildSummaryText(env, group, tournament, stage, resultRows, oddsMap);
    if (!text) {
      continue;
    }
    if (await sendTelegramMessage(token, chatId, text)) {
      await markDailySummarySent(sql, key, day, group, tournament, status, stage);
      sent += 1;
    }
  }
  return sent;
}

function targetGroups(events) {
  const grouped = new Map();
  for (const event of events) {
    if (!isTargetEvent(event)) {
      continue;
    }
    const key = [event.tour_group || "", event.tournament_name || "", event.tournament_status || ""].join("|");
    if (!grouped.has(key)) {
      grouped.set(key, {
        group: event.tour_group || "",
        tournament: event.tournament_name || "",
        status: event.tournament_status || "",
        rows: []
      });
    }
    grouped.get(key).rows.push(event);
  }
  return [...grouped.values()].sort((a, b) => {
    const ar = Math.min(...a.rows.map((row) => Number(row.tournament_sort_rank || 9)));
    const br = Math.min(...b.rows.map((row) => Number(row.tournament_sort_rank || 9)));
    return ar - br || a.group.localeCompare(b.group) || a.tournament.localeCompare(b.tournament);
  });
}

async function isDailySummarySent(sql, key) {
  const rows = await sql`select 1 from daily_summaries where summary_key = ${key} limit 1`;
  return rows.length > 0;
}

async function markDailySummarySent(sql, key, day, group, tournament, status, stage) {
  await sql`
    insert into daily_summaries (summary_key, day, tour_group, tournament_name, tournament_status, stage, sent_at)
    values (${key}, ${day}, ${group || ""}, ${tournament || ""}, ${status || ""}, ${stage || ""}, now())
    on conflict (summary_key) do nothing
  `;
}

function buildSummaryText(env, group, tournament, stage, events, oddsMap) {
  const buckets = {
    expected: [],
    pickem: [],
    unexpected: [],
    sad: [],
    no_odds: []
  };
  const sorted = [...events].sort((a, b) => Number(a.start_ts || 0) - Number(b.start_ts || 0));
  for (const event of sorted) {
    const line = resultLine(event);
    if (!line) {
      continue;
    }
    const category = categoryFor(env, event, oddsMap.get(event.event_id));
    buckets[category].push(line);
  }
  if (!Object.values(buckets).some((rows) => rows.length)) {
    return "";
  }

  const sections = [
    ["expected", "\u{1F44C}\u{1F3FB} \u041e\u0436\u0438\u0434\u0430\u0435\u043c\u043e"],
    ["pickem", "\u{1F7F0}\u041a\u043e\u0433\u0434\u0430 \u0448\u0430\u043d\u0441\u044b 50/50"],
    ["unexpected", "\u26A1 \u041d\u0435\u043e\u0436\u0438\u0434\u0430\u043d\u043d\u043e"],
    ["sad", "\u{1F625}  \u0413\u0440\u0443\u0441\u0442\u043d\u043e"],
    ["no_odds", "\u0411\u0435\u0437 \u043a\u043e\u044d\u0444\u0444\u0438\u0446\u0438\u0435\u043d\u0442\u043e\u0432"]
  ];

  const lines = ["\u{1F4CA} \u0420\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442\u044b \u0438\u0433\u0440\u043e\u0432\u043e\u0433\u043e \u0434\u043d\u044f", "", header(tournament, group, stage)];
  for (const [key, title] of sections) {
    if (!buckets[key].length) {
      continue;
    }
    lines.push("", title, "", ...buckets[key]);
  }
  return lines.join("\n").trim();
}

function header(tournament, group, stage) {
  const emoji = group === "women" ? "\u{1F64B}\u{1F3FC}\u200D\u2640\uFE0F" : "\u{1F64B}\u{1F3FC}\u200D\u2642\uFE0F";
  const gender = group === "women" ? "\u0436\u0435\u043d\u0449\u0438\u043d\u044b" : "\u043c\u0443\u0436\u0447\u0438\u043d\u044b";
  const stageText = stage ? stage.toLowerCase() : "\u0438\u0433\u0440\u043e\u0432\u043e\u0439 \u0434\u0435\u043d\u044c";
  return `${emoji} ${tournament}, ${gender}, ${stageText}`;
}

function categoryFor(env, event, odds) {
  const winner = winnerSide(event);
  if (!winner) {
    return "no_odds";
  }
  const loser = winner === "home" ? "away" : "home";
  if (isRussianSide(env, event, loser)) {
    return "sad";
  }
  if (!odds?.home_odds || !odds?.away_odds) {
    return "no_odds";
  }

  const homeOdds = Number(odds.home_odds);
  const awayOdds = Number(odds.away_odds);
  if (homeOdds <= 1 || awayOdds <= 1) {
    return "no_odds";
  }
  const homeProb = 1 / homeOdds / (1 / homeOdds + 1 / awayOdds);
  const awayProb = 1 - homeProb;
  const pickemMargin = Number(env.SUMMARY_PICKEM_MARGIN || 0.08);
  if (Math.abs(homeProb - awayProb) <= pickemMargin) {
    return "pickem";
  }
  const favorite = homeOdds < awayOdds ? "home" : "away";
  return winner === favorite ? "expected" : "unexpected";
}

function resultLine(event) {
  const winner = winnerSide(event);
  if (!winner) {
    return "";
  }
  const loser = winner === "home" ? "away" : "home";
  const score = winnerSets(event, winner);
  if (!score) {
    return "";
  }
  return `${shortSide(event[`${winner}_name`] || "TBD")} \u2014 ${shortSide(event[`${loser}_name`] || "TBD")} ${score}`;
}

function winnerSide(event) {
  const code = event.raw?.winnerCode;
  if (String(code) === "1") {
    return "home";
  }
  if (String(code) === "2") {
    return "away";
  }

  const home = numberOrNull(event.raw?.homeScore?.current ?? event.raw?.homeScore?.display);
  const away = numberOrNull(event.raw?.awayScore?.current ?? event.raw?.awayScore?.display);
  if (home !== null && away !== null) {
    if (home > away) return "home";
    if (away > home) return "away";
  }
  return "";
}

function winnerSets(event, winner) {
  const home = event.raw?.homeScore || {};
  const away = event.raw?.awayScore || {};
  const parts = [];
  for (let idx = 1; idx <= 5; idx += 1) {
    let h = scoreValue(home, `period${idx}`);
    let a = scoreValue(away, `period${idx}`);
    if (h === null || a === null) {
      continue;
    }
    let ht = scoreValue(home, `period${idx}TieBreak`);
    let at = scoreValue(away, `period${idx}TieBreak`);
    if (winner === "away") {
      [h, a] = [a, h];
      [ht, at] = [at, ht];
    }
    let text = `${fmtNum(h)}:${fmtNum(a)}`;
    if (![null, 0, "0"].includes(ht) || ![null, 0, "0"].includes(at)) {
      text += ` (${fmtNum(ht || 0)}:${fmtNum(at || 0)})`;
    }
    parts.push(text);
  }
  return parts.join(", ");
}

function scoreValue(score, key) {
  const value = score[key];
  return value === undefined || value === null || value === "" ? null : value;
}

function fmtNum(value) {
  const number = Number(value);
  return Number.isFinite(number) && Number.isInteger(number) ? String(number) : String(value);
}

function shortSide(name) {
  const text = String(name || "").replace(/\u00a0/g, " ").trim();
  if (text.includes("/")) {
    return text.split("/").map((part) => shortPlayer(part)).filter(Boolean).join(" / ");
  }
  return shortPlayer(text);
}

function shortPlayer(part) {
  return String(part || "")
    .replace(/\s+[A-Z\u0410-\u042f\u0401]\.(?:\s*-\s*[A-Z\u0410-\u042f\u0401]\.)?$/u, "")
    .replace(/^([A-Z\u0410-\u042f\u0401])\.\s*(\S+)$/u, "$1.$2")
    .trim();
}

async function sendTelegramMessage(token, chatId, text) {
  const response = await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, text })
  });
  if (!response.ok) {
    console.log(`[tg] send failed: ${response.status} ${await response.text()}`);
    return false;
  }
  return true;
}

function normalizeEvents(data) {
  const rows = [];
  for (const raw of data?.events || []) {
    try {
      if (raw?.id) {
        rows.push(normalizeEvent(raw));
      }
    } catch (_error) {
    }
  }
  return rows;
}

function normalizeEvent(raw) {
  const group = tourGroup(raw);
  const category = classify(raw);
  const tournament = tournamentName(raw);
  const season = seasonName(raw);
  const [tournamentStatus, tournamentRank] = rankedStatus(category, tournament, season);
  return {
    event_id: Number(raw.id),
    custom_id: raw.customId,
    tournament_name: tournament,
    season_name: season,
    category,
    tournament_status: tournamentStatus,
    tournament_sort_rank: tournamentRank,
    tour_group: group,
    home_name: sideName(raw, "home"),
    away_name: sideName(raw, "away"),
    start_ts: Number.isInteger(raw.startTimestamp) ? raw.startTimestamp : null,
    status_type: String(raw.status?.type || "").toLowerCase(),
    raw
  };
}

function categoryName(raw) {
  const tournament = raw.tournament || {};
  const unique = tournament.uniqueTournament || {};
  const category = unique.category || tournament.category || {};
  return lower(category.name, category.slug);
}

function tournamentName(raw) {
  const tournament = raw.tournament || {};
  const unique = tournament.uniqueTournament || {};
  return String(unique.name || tournament.name || "").trim();
}

function seasonName(raw) {
  return String(raw.season?.name || "").trim();
}

function classify(raw) {
  const hay = lower(categoryName(raw), tournamentName(raw), seasonName(raw));
  if (containsAny(hay, ["itf", "m15", "m25", "m35", "m50", "w15", "w25", "w35", "w50", "w75", "w100"])) {
    return "ITF";
  }
  if (hay.includes("challenger") || hay.includes("\u0447\u0435\u043b\u043b\u0435\u043d\u0434\u0436\u0435\u0440")) {
    return "Challenger";
  }
  if (containsAny(hay, ["wta", "women", "female", "\u0436\u0435\u043d\u0449\u0438\u043d"])) {
    return "WTA";
  }
  if (containsAny(hay, ["atp", "men", "male", "\u043c\u0443\u0436\u0447\u0438\u043d"])) {
    return "ATP";
  }
  return "Other";
}

function tourGroup(raw) {
  if (["men", "women"].includes(raw.tour_group_hint)) {
    return raw.tour_group_hint;
  }
  const category = classify(raw);
  const hay = lower(categoryName(raw), tournamentName(raw), seasonName(raw));
  if (category === "WTA" || containsAny(hay, ["wta", "women", "female", "\u0436\u0435\u043d\u0449\u0438\u043d", "w15", "w25", "w35", "w50", "w75", "w100"])) {
    return "women";
  }
  if (category === "ATP" || category === "Challenger" || containsAny(hay, ["atp", "challenger", "\u0447\u0435\u043b\u043b\u0435\u043d\u0434\u0436\u0435\u0440", "men", "male", "\u043c\u0443\u0436\u0447\u0438\u043d", "m15", "m25", "m35", "m50"])) {
    return "men";
  }
  return "other";
}

function rankedStatus(category, tournament, season) {
  const hay = lower(category, tournament, season);
  if (category === "ITF" || containsAny(hay, ["itf", "m15", "m25", "m35", "m50", "w15", "w25", "w35", "w50", "w75", "w100"])) {
    return ["ITF", 5];
  }
  if (category === "Challenger" || hay.includes("challenger") || hay.includes("\u0447\u0435\u043b\u043b\u0435\u043d\u0434\u0436\u0435\u0440")) {
    return ["Challenger", 4];
  }
  if (containsAny(hay, GRAND_SLAM_TOURNAMENTS)) {
    return ["Grand Slam", 0];
  }
  if ((category === "ATP" || category === "WTA") && hay.includes("1000")) {
    return [`${category} 1000`, 1];
  }
  if ((category === "ATP" || category === "WTA") && hay.includes("500")) {
    return [`${category} 500`, 2];
  }
  if ((category === "ATP" || category === "WTA") && hay.includes("250")) {
    return [`${category} 250`, 3];
  }
  if (category === "ATP" && containsAny(hay, COMMON_1000_TOURNAMENTS.concat(ATP_1000_TOURNAMENTS))) {
    return ["ATP 1000", 1];
  }
  if (category === "WTA" && containsAny(hay, COMMON_1000_TOURNAMENTS.concat(WTA_1000_TOURNAMENTS))) {
    return ["WTA 1000", 1];
  }
  if (category === "ATP") {
    if (containsAny(hay, ATP_500_TOURNAMENTS)) {
      return ["ATP 500", 2];
    }
    return ["ATP 250", 3];
  }
  if (category === "WTA") {
    if (containsAny(hay, WTA_500_TOURNAMENTS)) {
      return ["WTA 500", 2];
    }
    return ["WTA 250", 3];
  }
  return [category || "Other", 6];
}

function sideName(raw, side) {
  const keys = side === "home" ? ["homePlayer", "homeCompetitor", "homeTeam", "home"] : ["awayPlayer", "awayCompetitor", "awayTeam", "away"];
  for (const key of keys) {
    const value = raw[key];
    if (value && typeof value === "object") {
      const name = value.name || value.shortName;
      if (name) {
        return String(name);
      }
    }
  }
  return "TBD";
}

function isTargetEvent(event) {
  if (!TARGET_CATEGORIES.has(event.category)) {
    return false;
  }
  if (!TARGET_RANKS.has(Number(event.tournament_sort_rank || 9))) {
    return false;
  }
  if (isDoubles(event)) {
    return false;
  }
  return true;
}

function isDoubles(event) {
  const hay = norm([event.raw?.flashscore_league, event.season_name, event.tournament_name].join(" "));
  return hay.includes("\u043f\u0430\u0440\u043d") || hay.includes("doubles");
}

function isFinished(event) {
  return ["finished", "retired", "cancelled", "walkover"].includes(String(event.status_type || event.raw?.status?.type || "").toLowerCase());
}

function commonStage(events) {
  const counts = new Map();
  for (const event of events) {
    const stage = normalizeStage(event.raw?.card_stage || event.raw?.flashscore_round || event.raw?.round || event.raw?.stage || "");
    if (!stage) {
      continue;
    }
    counts.set(stage, (counts.get(stage) || 0) + 1);
  }
  if (!counts.size) {
    return "\u0438\u0433\u0440\u043e\u0432\u043e\u0439 \u0434\u0435\u043d\u044c";
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1])[0][0];
}

function normalizeStage(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  return text
    .replace(/round of 128/i, "1/64 \u0444\u0438\u043d\u0430\u043b\u0430")
    .replace(/round of 64/i, "1/32 \u0444\u0438\u043d\u0430\u043b\u0430")
    .replace(/round of 32/i, "1/16 \u0444\u0438\u043d\u0430\u043b\u0430")
    .replace(/round of 16/i, "1/8 \u0444\u0438\u043d\u0430\u043b\u0430")
    .replace(/quarter-finals?/i, "1/4 \u0444\u0438\u043d\u0430\u043b\u0430")
    .replace(/semi-finals?/i, "1/2 \u0444\u0438\u043d\u0430\u043b\u0430")
    .replace(/final/i, "\u0444\u0438\u043d\u0430\u043b");
}

function isRussianSide(env, event, side) {
  const competitor = event.raw?.[side === "home" ? "homeCompetitor" : "awayCompetitor"] || {};
  const countries = Array.isArray(competitor.countries) ? competitor.countries.join(" ") : competitor.country || "";
  if (tokens(countries).some((token) => ["\u0440\u043e\u0441\u0441\u0438\u044f", "russia", "rus"].includes(token))) {
    return true;
  }
  const hints = new Set(RUSSIAN_NAME_HINTS.concat(splitList(env.SUMMARY_RUSSIAN_NAME_HINTS).map(norm)));
  const name = norm(event[`${side}_name`] || "");
  return [...hints].some((hint) => hint && name.includes(hint));
}

function matchOddsItem(event, oddsItems) {
  const homeTokens = sideTokens(event, "home");
  const awayTokens = sideTokens(event, "away");
  let best = null;
  let bestScore = -1;
  for (const item of oddsItems) {
    const delta = timeDeltaSeconds(event, item);
    if (delta > 18 * 60 * 60) {
      continue;
    }
    const direct = sameSide(homeTokens, item.home_team) && sameSide(awayTokens, item.away_team);
    const reverse = sameSide(homeTokens, item.away_team) && sameSide(awayTokens, item.home_team);
    if (!direct && !reverse) {
      continue;
    }
    const [homeOdds, awayOdds] = oddsPricesForEvent(event, item);
    if (!homeOdds || !awayOdds) {
      continue;
    }
    const score = 10 - Math.min(Math.floor(delta / 3600), 9);
    if (score > bestScore) {
      best = item;
      bestScore = score;
    }
  }
  return best;
}

function oddsPricesForEvent(event, oddsItem) {
  const homeTokens = sideTokens(event, "home");
  const awayTokens = sideTokens(event, "away");
  const homePrices = [];
  const awayPrices = [];
  for (const bookmaker of oddsItem.bookmakers || []) {
    for (const market of bookmaker.markets || []) {
      if (market.key !== "h2h") {
        continue;
      }
      for (const outcome of market.outcomes || []) {
        const price = Number(outcome.price);
        if (!Number.isFinite(price)) {
          continue;
        }
        if (sameSide(homeTokens, outcome.name)) {
          homePrices.push(price);
        } else if (sameSide(awayTokens, outcome.name)) {
          awayPrices.push(price);
        }
      }
    }
  }
  return [median(homePrices), median(awayPrices)];
}

function sideTokens(event, side) {
  const competitor = event.raw?.[side === "home" ? "homeCompetitor" : "awayCompetitor"] || {};
  return new Set(tokens([event[`${side}_name`], competitor.name, competitor.shortName, competitor.slug].join(" ")));
}

function sameSide(sideTokensSet, name) {
  const candidate = tokens(name);
  return candidate.some((token) => sideTokensSet.has(token));
}

function timeDeltaSeconds(event, oddsItem) {
  if (!event.start_ts || !oddsItem.commence_time) {
    return 1e9;
  }
  return Math.abs(event.start_ts * 1000 - new Date(oddsItem.commence_time).getTime()) / 1000;
}

function median(values) {
  if (!values.length) {
    return null;
  }
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

function summaryKey(day, group, tournament, status, stage) {
  return [day, group || "", status || "", tournament || "", stage || ""].join("|");
}

function requiresOdds(env) {
  return !["0", "false", "no", "off"].includes(String(env.SUMMARY_REQUIRE_ODDS || "1").toLowerCase());
}

function isAuthorized(request, url, env) {
  if (!env.CRON_SECRET) {
    return false;
  }
  return request.headers.get("x-cron-secret") === env.CRON_SECRET || url.searchParams.get("secret") === env.CRON_SECRET;
}

function envShape(env) {
  const keys = [
    "DATABASE_URL",
    "POSTGRES_URL",
    "TELEGRAM_BOT_TOKEN",
    "PUBLISH_CHAT_ID",
    "SUMMARY_CHAT_ID",
    "SUMMARY_ENABLED",
    "SUMMARY_ODDS_SOURCE",
    "SUMMARY_REQUIRE_ODDS",
    "SUMMARY_PICKEM_MARGIN",
    "FLASHSCORE_ODDS_ENABLED",
    "FLASHSCORE_ODDS_REFRESH_MINUTES",
    "FLASHSCORE_ODDS_FETCH_LIMIT",
    "FLASHSCORE_BASE",
    "ODDS_API_KEY",
    "ODDS_API_MARKETS",
    "ODDS_API_REGIONS",
    "ODDS_REFRESH_MINUTES",
    "CRON_SECRET",
    "APP_TZ"
  ];
  return Object.fromEntries(
    keys.map((key) => [
      key,
      {
        present: Boolean(env[key]),
        length: env[key] ? String(env[key]).length : 0
      }
    ])
  );
}

function json(value, status = 200) {
  return new Response(JSON.stringify(value, null, 2), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" }
  });
}

function splitList(value) {
  return String(value || "").split(",").map((item) => item.trim()).filter(Boolean);
}

function containsAny(hay, needles) {
  return needles.some((needle) => hay.includes(needle));
}

function lower(...parts) {
  return norm(parts.filter((part) => part !== undefined && part !== null).join(" "));
}

function norm(value) {
  return String(value || "")
    .normalize("NFKD")
    .replace(/\p{Diacritic}/gu, "")
    .toLowerCase()
    .replace(/\u0451/g, "\u0435")
    .trim();
}

function tokens(value) {
  return norm(value).match(/[\p{L}\p{N}]+/gu) || [];
}

function numberOrNull(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function localDate(timeZone, date = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit"
  }).formatToParts(date);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

function addDays(dayIso, amount) {
  const date = new Date(`${dayIso}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() + amount);
  return date.toISOString().slice(0, 10);
}

function dayWindowUtc(dayIso, timeZone) {
  const [year, month, day] = dayIso.split("-").map(Number);
  const start = zonedDateTimeToUtc(timeZone, year, month, day, 0, 0, 0);
  const endIso = addDays(dayIso, 1);
  const [endYear, endMonth, endDay] = endIso.split("-").map(Number);
  const end = zonedDateTimeToUtc(timeZone, endYear, endMonth, endDay, 0, 0, 0);
  return [start.toISOString().replace(".000Z", "Z"), end.toISOString().replace(".000Z", "Z")];
}

function zonedDateTimeToUtc(timeZone, year, month, day, hour, minute, second) {
  const guess = Date.UTC(year, month - 1, day, hour, minute, second);
  const offset = timeZoneOffsetMs(timeZone, new Date(guess));
  return new Date(guess - offset);
}

function timeZoneOffsetMs(timeZone, date) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23"
  }).formatToParts(date);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  const asUtc = Date.UTC(
    Number(values.year),
    Number(values.month) - 1,
    Number(values.day),
    Number(values.hour),
    Number(values.minute),
    Number(values.second)
  );
  return asUtc - date.getTime();
}
