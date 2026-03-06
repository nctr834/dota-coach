export async function scoreTeams(radiant, dire) {
  const toPayload = (heroes) =>
    heroes
      .map((h, i) => (h ? {displayName: h.displayName, id: h.id, score: 0, pos: i + 1 } : null))
      .filter(Boolean)

  const res = await fetch('/api/score-teams', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      radiant: toPayload(radiant),
      dire: toPayload(dire),
    }),
  })
  return res.json()
}

export async function rankPicks(team, enemyTeam, pos) {
  const toPayload = (heroes) =>
    heroes.map((h, i) => (h ? {displayName: h.displayName, id: h.id, score: 0, pos: i + 1 } : null)).filter(Boolean)
  const res = await fetch('/api/rank-picks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      team: toPayload(team),
      enemyTeam: toPayload(enemyTeam),
      pos: pos,
    }),
  })
  return res.json()
}

export async function processQuery(query, team, enemyTeam, pick, pos, mySide) {
  const toPayload = (heroes) =>
    heroes
      .map((h, i) => (h ? {displayName: h.displayName, id: h.id, score: 0, pos: i + 1 } : null))
      .filter(Boolean)
  const res = await fetch('/api/query', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      query: query,
      team: toPayload(team),
      enemyTeam: toPayload(enemyTeam),
      pick: pick,
      pos: pos,
      mySide: mySide,
    }),
  })
  console.log(res)
  return res.json()
}