export async function scoreTeams(radiant, dire) {
  const toPayload = (heroes) =>
    heroes
      .map((h, i) =>
        h
          ? { displayName: h.displayName, id: h.id, score: 0, pos: i + 1 }
          : null,
      )
      .filter(Boolean);

  const res = await fetch("/api/score-teams", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      radiant: toPayload(radiant),
      dire: toPayload(dire),
    }),
  });
  return res.json();
}

export async function rankPicks(team, enemyTeam, pos) {
  const toPayload = (heroes) =>
    heroes
      .map((h, i) =>
        h
          ? { displayName: h.displayName, id: h.id, score: 0, pos: i + 1 }
          : null,
      )
      .filter(Boolean);
  const res = await fetch("/api/rank-picks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      team: toPayload(team),
      enemyTeam: toPayload(enemyTeam),
      pos: pos,
    }),
  });
  return res.json();
}

export async function processQuery(query, team, enemyTeam, pick, pos, mySide) {
  const toPayload = (heroes) =>
    heroes
      .map((h, i) =>
        h
          ? { displayName: h.displayName, id: h.id, score: 0, pos: i + 1 }
          : null,
      )
      .filter(Boolean);
  const res = await fetch("/api/query", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      query: query,
      team: toPayload(team),
      enemyTeam: toPayload(enemyTeam),
      pick: pick,
      pos: pos,
      mySide: mySide,
    }),
  });
  console.log(res);
  return res.json();
}

export async function reviewMatch(accountId, matchId) {
  const res = await fetch("/api/review-match", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ accountId, matchId }),
  });
  return res.json();
}

export async function getMatchDraftScore(matchId) {
  const res = await fetch(`/api/match-draft-score?matchId=${matchId}`);
  return res.json();
}

export async function fetchMatch(matchId) {
  const res = await fetch(`https://api.opendota.com/api/matches/${matchId}`);
  return res.json();
}

let rawItemsCache = null;
async function fetchItemConstants() {
  if (!rawItemsCache) {
    const res = await fetch("https://api.opendota.com/api/constants/items");
    rawItemsCache = await res.json();
  }
  return rawItemsCache;
}

let itemsCache = null;
export async function fetchItems() {
  if (!itemsCache) {
    const data = await fetchItemConstants();
    itemsCache = {};
    for (const [short, v] of Object.entries(data)) {
      if (v?.id != null) itemsCache[v.id] = { short, dname: v.dname || short };
    }
  }
  return itemsCache;
}

let itemsByShortCache = null;
export async function fetchItemsByShort() {
  if (!itemsByShortCache) {
    const data = await fetchItemConstants();
    itemsByShortCache = {};
    for (const [short, v] of Object.entries(data)) {
      itemsByShortCache[short] = {
        dname: v?.dname || short,
        cost: v?.cost,
        qual: v?.qual,
      };
    }
  }
  return itemsByShortCache;
}

export async function getChatHistory(accountId, matchId) {
  const res = await fetch(
    `/api/chat-history?accountId=${accountId}&matchId=${matchId}`,
  );
  return res.json();
}

export async function getReviewHistory(accountId) {
  const res = await fetch(`/api/review-history?accountId=${accountId}`);
  return res.json();
}

export async function chatMatch(accountId, matchId, message) {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ accountId, matchId, message }),
  });
  return res.json();
}

