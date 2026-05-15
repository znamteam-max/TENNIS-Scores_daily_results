const PROJECT_ID = "2";
const FEED_SIGN = "SW9D1eZo";
const DEFAULT_FLASHSCORE_BASE = "https://www.flashscore.com";
const DEFAULT_MATCH_ID = "Sril3X2m";
const DEFAULT_MATCH_URL =
  "https://www.flashscore.com/match/tennis/jasika-omar-lOWZLw6o/stewart-hamish-0j2A0w2n/?mid=Sril3X2m";

const stageFallback = {
  "1": "Scheduled",
  "2": "Live",
  "3": "Finished",
  "17": "Set 1",
  "18": "Set 2",
  "19": "Set 3",
  "20": "Set 4",
  "21": "Set 5",
  "42": "Awaiting updates",
  "45": "To finish"
};

export async function handleLiveOverlayRequest(request, env) {
  const url = new URL(request.url);

  if (url.pathname === "/overlay.html") {
    return html(OVERLAY_HTML);
  }
  if (url.pathname === "/overlay.css") {
    return text(OVERLAY_CSS, "text/css; charset=utf-8");
  }
  if (url.pathname === "/overlay.js") {
    return text(OVERLAY_JS, "text/javascript; charset=utf-8");
  }
  if (url.pathname === "/api/matches") {
    return json(defaultMatches(request));
  }
  if (url.pathname === "/api/news/tennis") {
    return json({
      items: [
        { title: "Tennis live overlay: счет обновляется автоматически" },
        { title: "OBS / Streamlabs / vMix: используйте Browser Source 1920x1080" },
        { title: "Flashscore adapter можно заменить на официальный источник данных" }
      ]
    });
  }
  if (url.pathname === "/api/match/flashscore") {
    try {
      return json(await getFlashscoreMatch(url, env), 200, {
        "cache-control": "public, max-age=2"
      });
    } catch (error) {
      return json({ ok: false, error: error?.message || String(error) }, 502);
    }
  }

  return null;
}

function defaultMatches(request) {
  const origin = new URL(request.url).origin;
  return [
    {
      id: `flashscore-${DEFAULT_MATCH_ID}`,
      title: "Omar Jasika - Hamish Stewart",
      description: "Flashscore live, Challenger Bengaluru 2",
      provider: "flashscore",
      source: `${origin}/api/match/flashscore?id=${DEFAULT_MATCH_ID}`,
      news: `${origin}/api/news/tennis`
    }
  ];
}

async function getFlashscoreMatch(url, env) {
  const eventId = extractEventId(url.searchParams.get("id"), url.searchParams.get("url"));
  const base = String(env.FLASHSCORE_LIVE_BASE || DEFAULT_FLASHSCORE_BASE).replace(/\/+$/, "");
  const matchUrl = normalizeMatchUrl(base, url.searchParams.get("url"), eventId);
  const feedBase = `${base}/x/feed/`;

  const [page, commonText, summaryText, statsText, historyText] = await Promise.all([
    fetchText(matchUrl, matchUrl),
    fetchText(`${feedBase}dc_${PROJECT_ID}_${eventId}`, matchUrl),
    fetchText(`${feedBase}df_sui_${PROJECT_ID}_${eventId}`, matchUrl),
    fetchText(`${feedBase}df_st_${PROJECT_ID}_${eventId}`, matchUrl),
    fetchText(`${feedBase}df_mh_${PROJECT_ID}_${eventId}`, matchUrl)
  ]);

  const common = parseFeed(commonText)[0] || {};
  const summary = parseSummary(parseFeed(summaryText));
  const history = parseMatchHistory(parseFeed(historyText));
  const players = extractPlayers(page);
  const servingSide = history.currentGame?.server || "";
  const stageMap = extractStageMap(page);
  const title = extractMeta(page, "og:title") || players.map((player) => player.name).join(" - ");
  const tournament = extractMeta(page, "og:description");

  return {
    schemaVersion: "1.0",
    provider: "flashscore",
    generatedAt: new Date().toISOString(),
    source: {
      eventId,
      url: matchUrl,
      feeds: {
        common: `dc_${PROJECT_ID}_${eventId}`,
        summary: `df_sui_${PROJECT_ID}_${eventId}`,
        statistics: `df_st_${PROJECT_ID}_${eventId}`,
        matchHistory: `df_mh_${PROJECT_ID}_${eventId}`
      }
    },
    match: {
      id: eventId,
      title,
      tournament,
      status: value(common, "DL") === "3" ? "live" : "unknown",
      stage: stageMap[value(common, "DB")] || summary.label || "Live",
      duration: summary.duration,
      startedAtUnix: value(common, "DC"),
      updatedAtUnix: value(common, "DD")
    },
    players: players.map((player) => ({
      ...player,
      isServing: player.side === servingSide
    })),
    score: {
      current: {
        home: value(common, "DP"),
        away: value(common, "DQ")
      },
      games: {
        home: value(common, "DN", summary.homeGames),
        away: value(common, "DO", summary.awayGames)
      },
      sets: deriveSets(summary, common)
    },
    statistics: parseStats(parseFeed(statsText)),
    matchHistory: history.games,
    currentGame: history.currentGame
  };
}

async function fetchText(url, referer) {
  const response = await fetch(url, {
    headers: {
      "user-agent": flashscoreUserAgent(),
      accept: "*/*",
      "accept-language": "en-US,en;q=0.9",
      referer,
      "x-fsign": FEED_SIGN
    }
  });
  if (!response.ok) {
    throw new Error(`Flashscore ${response.status}: ${url}`);
  }
  return response.text();
}

function extractEventId(id, sourceUrl) {
  if (id) return String(id).trim();
  if (!sourceUrl) return DEFAULT_MATCH_ID;

  const parsed = new URL(sourceUrl);
  const mid = parsed.searchParams.get("mid");
  if (mid) return mid;

  const pathMatch = parsed.pathname.match(/-([A-Za-z0-9]{8})(?:\/|$)/);
  if (pathMatch) return pathMatch[1];

  throw new Error("Pass ?id=... or a Flashscore URL with ?mid=...");
}

function normalizeMatchUrl(base, sourceUrl, eventId) {
  if (sourceUrl) return sourceUrl;
  if (eventId === DEFAULT_MATCH_ID) return DEFAULT_MATCH_URL;
  return `${base}/match/tennis/live-match/?mid=${encodeURIComponent(eventId)}`;
}

function splitRecord(record) {
  const fields = {};
  for (const token of record.split("¬")) {
    if (!token.includes("÷")) continue;
    const index = token.indexOf("÷");
    fields[token.slice(0, index)] = token.slice(index + 1);
  }
  return fields;
}

function parseFeed(text) {
  return String(text || "")
    .split("¬~")
    .map((record) => record.replace(/~$/g, "").trim())
    .filter(Boolean)
    .map(splitRecord);
}

function value(record, key, fallback = "") {
  return record?.[key] ?? fallback;
}

function extractBalancedJson(text, marker) {
  const markerIndex = text.indexOf(marker);
  if (markerIndex < 0) return null;

  const start = text.indexOf("{", markerIndex + marker.length);
  if (start < 0) return null;

  let depth = 0;
  let inString = false;
  let escaped = false;

  for (let index = start; index < text.length; index += 1) {
    const char = text[index];
    if (inString) {
      if (escaped) escaped = false;
      else if (char === "\\") escaped = true;
      else if (char === '"') inString = false;
      continue;
    }
    if (char === '"') inString = true;
    else if (char === "{") depth += 1;
    else if (char === "}") depth -= 1;
    if (depth === 0) return text.slice(start, index + 1);
  }

  return null;
}

function safeJson(jsonText) {
  if (!jsonText) return null;
  try {
    return JSON.parse(jsonText);
  } catch (_error) {
    return null;
  }
}

function extractMeta(htmlText, property) {
  const pattern = new RegExp(`<meta[^>]+(?:property|name)=["']${property}["'][^>]+content=["']([^"']*)["']`, "i");
  return htmlText.match(pattern)?.[1] || "";
}

function extractStageMap(htmlText) {
  return {
    ...stageFallback,
    ...(safeJson(extractBalancedJson(htmlText, '"eventStageTranslations":')) || {})
  };
}

function extractPlayers(htmlText) {
  const participants =
    safeJson(extractBalancedJson(htmlText, '"participantsData":')) ||
    safeJson(extractBalancedJson(htmlText, '"participants":')) ||
    {};
  const home = participants.home?.[0] || {};
  const away = participants.away?.[0] || {};

  return [
    {
      side: "home",
      id: home.id || "",
      name: home.full_name || home.seo_name || home.name || "Home player",
      shortName: home.name || home.short_name || home.full_name || "Home",
      country: home.country || "",
      rank: Array.isArray(home.rank) ? home.rank[1] || "" : "",
      image: home.image_path || ""
    },
    {
      side: "away",
      id: away.id || "",
      name: away.full_name || away.seo_name || away.name || "Away player",
      shortName: away.name || away.short_name || away.full_name || "Away",
      country: away.country || "",
      rank: Array.isArray(away.rank) ? away.rank[1] || "" : "",
      image: away.image_path || ""
    }
  ];
}

function parseSummary(records) {
  const setRecord = records.find((record) => record.AC && (record.IG || record.IH)) || {};
  return {
    label: value(setRecord, "AC", ""),
    homeGames: value(setRecord, "IG", ""),
    awayGames: value(setRecord, "IH", ""),
    duration: value(setRecord, "RC", records.find((record) => record.RB)?.RB || "")
  };
}

function parseMatchHistory(records) {
  const games = [];
  let currentSet = "";
  let currentGame = null;

  for (const record of records) {
    if (record.HA) currentSet = record.HA;
    if (record.HC || record.HE) {
      games.push({
        set: currentSet,
        homeGames: value(record, "HC"),
        awayGames: value(record, "HE"),
        server: value(record, "HG") === "1" ? "home" : value(record, "HG") === "2" ? "away" : "",
        winner: value(record, "HK") === "1" ? "home" : value(record, "HK") === "2" ? "away" : "",
        breakPoint: value(record, "HH") === "1",
        points: value(record, "HL")
      });
    }
    if (record.HN || record.HO) {
      currentGame = {
        server: value(record, "HN") === "1" ? "home" : value(record, "HN") === "2" ? "away" : "",
        points: value(record, "HO"),
        currentPoint: value(record, "HO").split(",").map((item) => item.trim()).filter(Boolean).at(-1) || ""
      };
    }
  }

  return { games, currentGame };
}

function parseStats(records) {
  const sections = [];
  let scope = "";
  let section = null;

  for (const record of records) {
    if (record.SE) {
      scope = record.SE;
      section = null;
      continue;
    }
    if (scope && scope !== "Match") continue;
    if (record.SF) {
      section = { section: record.SF, rows: [] };
      sections.push(section);
      continue;
    }
    if (record.SG) {
      if (!section) {
        section = { section: "Match", rows: [] };
        sections.push(section);
      }
      section.rows.push({
        label: record.SG,
        home: value(record, "SH"),
        away: value(record, "SI")
      });
    }
  }

  return sections;
}

function deriveSets(summary, common) {
  const homeGames = value(common, "DN", summary.homeGames);
  const awayGames = value(common, "DO", summary.awayGames);
  if (!summary.label && !homeGames && !awayGames) return [];
  return [{ label: summary.label || "Current set", homeGames, awayGames, winner: "" }];
}

function flashscoreUserAgent() {
  return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36";
}

function json(value, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(value, null, 2), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "access-control-allow-origin": "*",
      ...extraHeaders
    }
  });
}

function html(body) {
  return text(body, "text/html; charset=utf-8");
}

function text(body, contentType) {
  return new Response(body, {
    headers: {
      "content-type": contentType,
      "cache-control": "public, max-age=60"
    }
  });
}

const OVERLAY_HTML = `<!doctype html>
<html lang="ru">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Tennis Live Overlay</title>
    <link rel="stylesheet" href="/overlay.css">
  </head>
  <body>
    <main id="overlay" class="overlay">
      <aside class="left-rail">
        <section class="promo-block">
          <div class="promo-kicker">LIVE TENNIS</div>
          <div id="matchTitle" class="promo-title">Loading match</div>
          <div id="matchStage" class="promo-meta">Connecting data</div>
        </section>
        <section id="statsPanel" class="stats-panel">
          <div class="panel-head"><span>Статистика</span><span id="statUpdated">--:--</span></div>
          <div id="statsList" class="stats-list"></div>
        </section>
        <section id="chatPanel" class="chat-panel" hidden>
          <div class="panel-head"><span>Чат</span><span>LIVE</span></div>
          <div class="chat-line">Комментатор подключен.</div>
          <div class="chat-line">Оверлей готов к эфиру.</div>
          <div class="chat-line muted">Здесь можно вывести чат трансляции.</div>
        </section>
        <section class="brand-block"><span>БОЛЬШЕ!</span><small>TENNIS STREAM</small></section>
      </aside>
      <section class="video-zone"><div class="safe-label">VIDEO / COMMENTATOR AREA</div></section>
      <section class="scorebug">
        <div id="tournament" class="scorebug-tournament">Tournament</div>
        <div id="playerHome" class="player-row"><span class="serve-dot"></span><span class="player-name">Home</span><span id="homeGames" class="score-cell">0</span><span id="homePoint" class="point-cell">0</span></div>
        <div id="playerAway" class="player-row"><span class="serve-dot"></span><span class="player-name">Away</span><span id="awayGames" class="score-cell">0</span><span id="awayPoint" class="point-cell">0</span></div>
      </section>
      <section class="ticker"><div id="tickerTrack" class="ticker-track">Loading news...</div></section>
    </main>
    <script src="/overlay.js"></script>
  </body>
</html>`;

const OVERLAY_CSS = `:root{--green:#caff3d;--cyan:#18d8c8;--panel:rgba(17,20,25,.88);--line:rgba(255,255,255,.16);--text:#f7f9fc;--muted:#aab3bf}*{box-sizing:border-box}html,body{width:100%;height:100%;margin:0;overflow:hidden;background:transparent;color:var(--text);font-family:Arial,Helvetica,sans-serif}.overlay{position:relative;width:100vw;height:100vh;min-width:1280px;min-height:720px}.left-rail{position:absolute;left:34px;top:34px;bottom:78px;width:360px;display:grid;grid-template-rows:164px minmax(0,1fr) 128px;gap:18px;min-height:0}.promo-block,.stats-panel,.chat-panel,.brand-block,.scorebug{border:1px solid var(--line);border-radius:8px;background:var(--panel);box-shadow:0 20px 50px rgba(0,0,0,.28);backdrop-filter:blur(8px)}.stats-panel,.chat-panel{min-height:0;overflow:hidden}.promo-block{padding:22px;border-left:6px solid var(--green)}.promo-kicker{color:var(--green);font-size:15px;font-weight:800}.promo-title{margin-top:18px;font-size:28px;line-height:1.05;font-weight:800}.promo-meta{margin-top:10px;color:var(--muted);font-size:15px}.panel-head{display:flex;justify-content:space-between;align-items:center;min-height:44px;padding:0 16px;border-bottom:1px solid var(--line);color:var(--green);font-weight:800;font-size:14px}.stats-list{height:calc(100% - 44px);overflow:hidden;padding:12px 14px}.stat-section{margin-bottom:13px}.stat-section-title{margin-bottom:7px;color:var(--cyan);font-size:13px;font-weight:800}.stat-row{display:grid;grid-template-columns:58px 1fr 58px;align-items:center;min-height:25px;gap:8px;color:var(--text);font-size:13px}.stat-row span:nth-child(2){color:var(--muted);text-align:center}.stat-row span:last-child{text-align:right}.chat-line{margin:14px 16px 0;padding:10px 12px;border-radius:8px;background:rgba(255,255,255,.08);font-size:15px}.chat-line.muted{color:var(--muted)}.brand-block{display:grid;align-content:center;justify-items:start;padding:0 26px;background:linear-gradient(90deg,rgba(255,77,109,.92),rgba(202,255,61,.88));color:#101114}.brand-block span{font-size:40px;font-weight:900}.brand-block small{margin-top:4px;font-weight:800}.video-zone{position:absolute;left:430px;right:34px;top:34px;bottom:78px;border:1px dashed transparent}.safe-label{position:absolute;top:0;right:0;opacity:0;color:rgba(255,255,255,.5);font-size:13px}.scorebug{position:absolute;right:44px;bottom:106px;width:462px;padding:12px}.scorebug-tournament{min-height:24px;margin-bottom:8px;color:var(--green);font-size:13px;font-weight:800;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.player-row{display:grid;grid-template-columns:18px minmax(0,1fr) 54px 66px;align-items:center;min-height:42px;gap:10px;border-top:1px solid var(--line);font-size:18px;font-weight:800}.serve-dot{width:10px;height:10px;border-radius:50%;background:transparent}.player-row.serving .serve-dot{background:var(--green);box-shadow:0 0 18px var(--green)}.player-name{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.score-cell,.point-cell{min-height:30px;display:inline-flex;align-items:center;justify-content:center;border-radius:6px;background:rgba(255,255,255,.1)}.point-cell{background:var(--green);color:#101114}.ticker{position:absolute;left:0;right:0;bottom:0;height:52px;overflow:hidden;background:#050608;border-top:3px solid var(--green)}.ticker-track{display:inline-flex;align-items:center;height:52px;min-width:100%;padding-left:100%;white-space:nowrap;color:var(--text);font-size:23px;font-weight:800;animation:ticker 38s linear infinite}@keyframes ticker{from{transform:translateX(0)}to{transform:translateX(-100%)}}.guides .left-rail,.guides .video-zone,.guides .scorebug,.guides .ticker{outline:2px solid rgba(202,255,61,.75)}.guides .safe-label{opacity:1}`;

const OVERLAY_JS = `const params=new URLSearchParams(window.location.search);const config={source:params.get("source")||"/api/match/flashscore?id=${DEFAULT_MATCH_ID}",news:params.get("news")||"/api/news/tennis",panel:params.get("panel")||"stats",poll:Number(params.get("poll")||3000),guides:params.get("guides")==="1"};const refs={overlay:document.querySelector("#overlay"),matchTitle:document.querySelector("#matchTitle"),matchStage:document.querySelector("#matchStage"),tournament:document.querySelector("#tournament"),statsPanel:document.querySelector("#statsPanel"),chatPanel:document.querySelector("#chatPanel"),statsList:document.querySelector("#statsList"),statUpdated:document.querySelector("#statUpdated"),playerHome:document.querySelector("#playerHome"),playerAway:document.querySelector("#playerAway"),homeName:document.querySelector("#playerHome .player-name"),awayName:document.querySelector("#playerAway .player-name"),homeGames:document.querySelector("#homeGames"),awayGames:document.querySelector("#awayGames"),homePoint:document.querySelector("#homePoint"),awayPoint:document.querySelector("#awayPoint"),tickerTrack:document.querySelector("#tickerTrack")};function asText(value,fallback=""){return value===null||value===undefined||value===""?fallback:String(value)}function setPanelMode(){refs.statsPanel.hidden=config.panel==="chat";refs.chatPanel.hidden=config.panel!=="chat"}function setGuides(){refs.overlay.classList.toggle("guides",config.guides)}async function fetchJson(url){const response=await fetch(url,{cache:"no-store"});if(!response.ok)throw new Error(response.status+" "+response.statusText);return response.json()}function statRows(sections){const wanted=["Service","Return","Points","Games"];return sections.filter(section=>wanted.includes(section.section)).slice(0,4).map(section=>{const rows=section.rows.slice(0,section.section==="Points"?4:3).map(row=>'<div class="stat-row"><span>'+asText(row.home,"-")+'</span><span>'+row.label+'</span><span>'+asText(row.away,"-")+"</span></div>").join("");return '<div class="stat-section"><div class="stat-section-title">'+section.section+"</div>"+rows+"</div>"}).join("")}function renderMatch(data){const home=data.players?.find(player=>player.side==="home")||data.players?.[0]||{};const away=data.players?.find(player=>player.side==="away")||data.players?.[1]||{};refs.matchTitle.textContent=data.match?.title||asText(home.name,"Home")+" - "+asText(away.name,"Away");refs.matchStage.textContent=[data.match?.stage,data.match?.duration].filter(Boolean).join(" · ");refs.tournament.textContent=data.match?.tournament||data.match?.stage||"Live tennis";refs.homeName.textContent=asText(home.shortName||home.name,"Home");refs.awayName.textContent=asText(away.shortName||away.name,"Away");refs.homeGames.textContent=asText(data.score?.games?.home,"0");refs.awayGames.textContent=asText(data.score?.games?.away,"0");refs.homePoint.textContent=asText(data.score?.current?.home,"");refs.awayPoint.textContent=asText(data.score?.current?.away,"");refs.playerHome.classList.toggle("serving",Boolean(home.isServing));refs.playerAway.classList.toggle("serving",Boolean(away.isServing));refs.statsList.innerHTML=statRows(data.statistics||[])||'<div class="chat-line muted">Статистика пока недоступна.</div>';refs.statUpdated.textContent=new Date(data.generatedAt||Date.now()).toLocaleTimeString("ru-RU",{hour:"2-digit",minute:"2-digit"})}function renderNews(items){const list=Array.isArray(items)?items:items.items||[];refs.tickerTrack.textContent=list.map(item=>item.title||item).filter(Boolean).join("   •   ")}async function refreshMatch(){try{renderMatch(await fetchJson(config.source))}catch(error){refs.matchStage.textContent="Ошибка данных: "+error.message}}async function refreshNews(){try{renderNews(await fetchJson(config.news))}catch(_error){refs.tickerTrack.textContent="Новости временно недоступны"}}setPanelMode();setGuides();refreshMatch();refreshNews();setInterval(refreshMatch,Math.max(config.poll,1000));setInterval(refreshNews,60000);`;
