import { useEffect, useState } from "react";
import heroes from "../data/hero_data.json";
import { fetchItems, fetchItemsByShort, getMatchDraftScore } from "../api";

const HERO_BY_ID = {};
for (const h of Object.values(heroes)) HERO_BY_ID[Number(h.id)] = h;
const HERO_BY_NAME = {};
for (const h of Object.values(heroes))
  HERO_BY_NAME["npc_dota_hero_" + h.shortName] = h;

const CDN =
  "https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react";

const heroIconUrl = (heroId) => {
  const h = HERO_BY_ID[heroId];
  return h ? `${CDN}/heroes/${h.shortName}.png` : null;
};

const heroName = (heroId) =>
  HERO_BY_ID[heroId]?.displayName || `Hero ${heroId}`;

const fmtDuration = (s) =>
  `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
const fmtK = (n) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : `${n}`);
const fmtSigned = (n) => `${n < 0 ? "-" : "+"}${fmtK(Math.abs(n))}`;

const CARD = "rounded-xl border border-white/10 bg-white/[0.02] p-4";
const CARD_TITLE =
  "text-[10px] font-medium text-gray-600 uppercase tracking-widest";

const W = 600;
const H = 150;
const PAD = 8;

function useHoverIndex(len) {
  const [idx, setIdx] = useState(null);
  const handlers = {
    onMouseMove: (e) => {
      const r = e.currentTarget.getBoundingClientRect();
      const svgX = ((e.clientX - r.left) / r.width) * W;
      const f = (svgX - PAD) / (W - 2 * PAD);
      setIdx(Math.min(len - 1, Math.max(0, Math.round(f * (len - 1)))));
    },
    onMouseLeave: () => setIdx(null),
  };
  return [idx, handlers];
}

function Legend({ entries }) {
  return (
    <div className="flex gap-4 mt-1 text-[10px] text-gray-500">
      {entries.map(([label, color]) => (
        <span key={label} className="flex items-center gap-1.5">
          <span
            className="inline-block w-3 h-0.5"
            style={{ background: color }}
          />
          {label}
        </span>
      ))}
    </div>
  );
}

function ItemSlots({ player, items }) {
  const slot = (id, key, dim) => {
    const it = id ? items?.[id] : null;
    return (
      <div
        key={key}
        title={it?.dname}
        className={`w-7 h-5 rounded-sm bg-white/5 overflow-hidden shrink-0 ${dim ? "opacity-50" : ""}`}
      >
        {it && (
          <img
            src={`${CDN}/items/${it.short}.png`}
            alt={it.dname}
            className="w-full h-full object-cover"
          />
        )}
      </div>
    );
  };
  return (
    <div className="flex items-center gap-0.5">
      {[0, 1, 2, 3, 4, 5].map((i) => slot(player[`item_${i}`], `i${i}`, false))}
      {[0, 1, 2].map(
        (i) =>
          player[`backpack_${i}`] > 0 &&
          slot(player[`backpack_${i}`], `b${i}`, true),
      )}
    </div>
  );
}

function TeamTable({ name, color, draftScore, players, accountId, items }) {
  return (
    <div className="min-w-0 rounded-lg border border-white/5 bg-white/[0.03] p-3">
      <div className="flex items-baseline justify-between mb-1.5">
        <div
          className={`text-[10px] font-medium uppercase tracking-widest ${color}`}
        >
          {name}
        </div>
        {draftScore != null && (
          <div className="flex items-baseline gap-1.5">
            <span className="text-[10px] text-gray-600 uppercase tracking-widest">
              Draft
            </span>
            <span className={`text-xs font-medium ${color}`}>
              {draftScore.toFixed(1)}
            </span>
          </div>
        )}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-gray-600 text-left">
              <th className="font-medium pb-1">Hero</th>
              <th className="font-medium pb-1 pl-4 text-right">K/D/A</th>
              <th className="font-medium pb-1 pl-4 text-right">LH</th>
              <th className="font-medium pb-1 pl-4 text-right">GPM</th>
              <th className="font-medium pb-1 pl-4 text-right">DMG</th>
              <th className="font-medium pb-1 pl-4 text-right">NW</th>
              <th className="font-medium pb-1 pl-4">Items</th>
            </tr>
          </thead>
          <tbody>
            {players.map((p) => {
              const me = accountId != null && p.account_id === accountId;
              return (
                <tr
                  key={p.player_slot}
                  className={me ? "bg-indigo-500/10" : ""}
                >
                  <td className="py-1">
                    <div className="flex items-center gap-2">
                      {heroIconUrl(p.hero_id) && (
                        <img
                          src={heroIconUrl(p.hero_id)}
                          alt=""
                          className="w-8 h-[18px] rounded-sm object-cover shrink-0"
                        />
                      )}
                      <div className="leading-tight">
                        <div
                          className={`whitespace-nowrap ${me ? "text-indigo-300" : "text-gray-300"}`}
                        >
                          {heroName(p.hero_id)}
                        </div>
                        <div className="text-[10px] text-gray-600 truncate max-w-[110px]">
                          {p.personaname || "Anonymous"}
                        </div>
                      </div>
                    </div>
                  </td>
                  <td className="py-1 pl-4 text-right text-gray-300 whitespace-nowrap">
                    {p.kills}/{p.deaths}/{p.assists}
                  </td>
                  <td className="py-1 pl-4 text-right text-gray-400">
                    {p.last_hits}
                  </td>
                  <td className="py-1 pl-4 text-right text-gray-400">
                    {p.gold_per_min}
                  </td>
                  <td className="py-1 pl-4 text-right text-gray-400">
                    {fmtK(p.hero_damage)}
                  </td>
                  <td className="py-1 pl-4 text-right text-gray-400">
                    {fmtK(p.net_worth)}
                  </td>
                  <td className="py-1 pl-4">
                    <ItemSlots player={p} items={items} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function MatchSummary({ match, accountId }) {
  const [items, setItems] = useState(null);
  const [draft, setDraft] = useState(null);
  useEffect(() => {
    fetchItems()
      .then(setItems)
      .catch(() => { });
  }, []);
  useEffect(() => {
    let on = true;
    getMatchDraftScore(match.match_id)
      .then((d) => on && setDraft({ ...d, matchId: match.match_id }))
      .catch(() => { });
    return () => {
      on = false;
    };
  }, [match.match_id]);
  const score = draft && draft.matchId === match.match_id ? draft : null;

  const radiant = match.players.filter((p) => p.isRadiant);
  const dire = match.players.filter((p) => !p.isRadiant);
  return (
    <div className={`${CARD} space-y-4`}>
      <div className="flex items-center justify-center gap-4">
        <span className="text-sm font-medium text-green-400">
          {match.radiant_score}
        </span>
        <div className="text-center">
          <div
            className={`text-sm font-semibold ${match.radiant_win ? "text-green-400" : "text-red-400"}`}
          >
            {match.radiant_win ? "Radiant Victory" : "Dire Victory"}
          </div>
          <div className="text-[10px] text-gray-600 uppercase tracking-widest">
            {fmtDuration(match.duration)}
          </div>
        </div>
        <span className="text-sm font-medium text-red-400">
          {match.dire_score}
        </span>
      </div>
      {score && (
        <div className="flex items-center gap-3">
          <div className="flex-1 h-1.5 rounded-full bg-gray-800 overflow-hidden">
            <div
              className="h-full rounded-full"
              style={{
                width: `${Math.min(100, Math.max(0, 50 + score.delta))}%`,
                background:
                  score.delta >= 0
                    ? "linear-gradient(90deg, #22c55e, #4ade80)"
                    : "linear-gradient(90deg, #ef4444, #f87171)",
              }}
            />
          </div>
          <div
            className={`text-[10px] font-medium whitespace-nowrap ${score.delta > 0 ? "text-green-400" : score.delta < 0 ? "text-red-400" : "text-gray-400"}`}
          >
            {score.delta > 0
              ? "Radiant Favored"
              : score.delta < 0
                ? "Dire Favored"
                : "Even Draft"}
            {score.delta !== 0 && ` (${score.delta > 0 ? "+" : ""}${score.delta.toFixed(1)})`}
          </div>
        </div>
      )}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <TeamTable
          name="Radiant"
          color="text-green-400"
          draftScore={score?.radiantScore}
          players={radiant}
          accountId={accountId}
          items={items}
        />
        <TeamTable
          name="Dire"
          color="text-red-400"
          draftScore={score?.direScore}
          players={dire}
          accountId={accountId}
          items={items}
        />
      </div>
    </div>
  );
}

const GOLD_COLOR = "rgba(229,231,235,0.7)";
const XP_COLOR = "rgba(251,191,36,0.8)";

const DEATH_W = 16;
const DEATH_H = 9;
const ITEM_W = 16;
const ITEM_H = 12;
const ME_COLOR = "rgba(129,140,248,0.9)";
// Mirrors the backend's notable-item filter: completed tiers, real cost, and
// the Sange/Yasha/Kaya components that only matter as their upgrades. Valve
// marks the Blink family "component", so it needs the same override the data
// pipeline applies.
const NOTABLE_QUAL = new Set(["rare", "epic", "artifact"]);
const COMPONENT_NOISE = new Set(["sange", "yasha", "kaya"]);
const QUAL_OVERRIDES = new Set([
  "blink",
  "overwhelming_blink",
  "swift_blink",
  "arcane_blink",
]);
const KILL_COLOR = "rgba(248,113,113,0.95)";
const ASSIST_COLOR = "rgba(250,204,21,0.9)";

export function AdvantageGraphCard({ match, accountId }) {
  const gold = match.radiant_gold_adv;
  const xp = match.radiant_xp_adv?.length > 1 ? match.radiant_xp_adv : null;
  const len = gold.length;
  const [hover, handlers] = useHoverIndex(len);
  const [showDeaths, setShowDeaths] = useState(true);
  const [showItems, setShowItems] = useState(true);
  const [itemsByShort, setItemsByShort] = useState(null);
  useEffect(() => {
    fetchItemsByShort()
      .then(setItemsByShort)
      .catch(() => { });
  }, []);

  const me = match.players.find((p) => p.account_id === accountId);
  const myRadiant = me ? me.isRadiant : true;
  const allyNpc = {};
  const enemyNpc = {};
  for (const p of match.players) {
    const npc = "npc_dota_hero_" + HERO_BY_ID[p.hero_id]?.shortName;
    if (p.isRadiant === myRadiant) allyNpc[npc] = p.hero_id;
    else enemyNpc[npc] = p.hero_id;
  }
  const deathsOf = (side) =>
    match.players
      .flatMap((p) => (p.kills_log || []).map((k) => ({ p, k })))
      .filter(({ k }) => side[k.key] != null)
      .map(({ p, k }) => ({
        time: Math.max(0, k.time),
        heroId: side[k.key],
        byMe: me != null && p.account_id === accountId,
      }))
      .sort((a, b) => a.time - b.time);
  const deaths = deathsOf(allyNpc);
  const enemyDeaths = deathsOf(enemyNpc);
  // ponytail: assist = enemy died in a teamfight I dealt damage in; OpenDota
  // has no per-death assist log, so pickoff assists outside fights go unmarked.
  const meIdx = match.players.findIndex((p) => p.account_id === accountId);
  const myFights = (match.teamfights || []).filter(
    (f) => meIdx >= 0 && f.players?.[meIdx]?.damage > 0,
  );
  const inMyFight = (t) => myFights.some((f) => f.start <= t && t <= f.end);
  const roshans = (match.objectives || [])
    .filter((o) => o.type === "CHAT_MESSAGE_ROSHAN_KILL")
    .map((o) => ({ min: Math.max(0, o.time) / 60, radiant: o.team === 2 }));
  const buys = [];
  if (me && itemsByShort) {
    const seen = new Set();
    for (const e of me.purchase_log || []) {
      const it = itemsByShort[e.key];
      if (!it || seen.has(e.key)) continue;
      if (!NOTABLE_QUAL.has(it.qual) && !QUAL_OVERRIDES.has(e.key)) continue;
      if ((it.cost || 0) < 1000 || COMPONENT_NOISE.has(e.key)) continue;
      seen.add(e.key);
      buys.push({ time: Math.max(0, e.time), short: e.key, name: it.dname });
    }
  }

  const max = Math.max(1, ...gold.map(Math.abs), ...(xp || []).map(Math.abs));
  const x = (i) => PAD + (i * (W - 2 * PAD)) / Math.max(1, len - 1);
  const y = (v) => H / 2 - (v / max) * (H / 2 - PAD);
  const area = (clamp) =>
    `M ${x(0)} ${H / 2} ` +
    gold
      .map((v, i) => `L ${x(i).toFixed(1)} ${y(clamp(v)).toFixed(1)}`)
      .join(" ") +
    ` L ${x(len - 1).toFixed(1)} ${H / 2} Z`;
  const line = (vals) =>
    vals.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");

  // Events close in time (one fight, back-to-back buys) share a column.
  const clusterX = (arr, w) => {
    const stacks = [];
    for (const d of arr) {
      const dx = x(d.time / 60);
      const last = stacks[stacks.length - 1];
      if (last && dx - last.x < w) last.items.push(d);
      else stacks.push({ x: dx, items: [d] });
    }
    return stacks;
  };
  // Ally deaths stack bottom-up in chronological order; enemy deaths mirror
  // them top-down; item buys sit in the top rows above the enemy stacks.
  const stacks = clusterX(deaths, DEATH_W);
  const enemyStacks = clusterX(enemyDeaths, DEATH_W);
  const itemStacks = clusterX(buys, ITEM_W);
  const atHover = (arr) =>
    hover != null ? arr.filter((d) => Math.round(d.time / 60) === hover) : [];
  const diedAtHover = atHover(deaths);
  const enemyDiedAtHover = atHover(enemyDeaths);
  const boughtAtHover = atHover(buys);

  return (
    <div className={CARD}>
      <div className="flex items-baseline justify-between mb-1.5">
        <div className={CARD_TITLE}>Radiant Advantage</div>
        <div className="flex items-baseline gap-3">
          {hover != null && (
            <div className="text-[10px] text-gray-400">
              {hover}m gold {fmtSigned(gold[hover])}
              {xp && xp[hover] != null ? ` / xp ${fmtSigned(xp[hover])}` : ""}
              {diedAtHover.length > 0 &&
                ` · died: ${diedAtHover.map((d) => heroName(d.heroId)).join(", ")}`}
              {enemyDiedAtHover.length > 0 &&
                ` · enemy died: ${enemyDiedAtHover.map((d) => heroName(d.heroId)).join(", ")}`}
              {boughtAtHover.length > 0 &&
                ` · bought: ${boughtAtHover.map((b) => b.name).join(", ")}`}
            </div>
          )}
          {buys.length > 0 && (
            <button
              onClick={() => setShowItems(!showItems)}
              className="text-[10px] uppercase tracking-widest text-gray-600 hover:text-gray-400 cursor-pointer"
            >
              {showItems ? "Hide items" : "Show items"}
            </button>
          )}
          <button
            onClick={() => setShowDeaths(!showDeaths)}
            className="text-[10px] uppercase tracking-widest text-gray-600 hover:text-gray-400 cursor-pointer"
          >
            {showDeaths ? "Hide deaths" : "Show deaths"}
          </button>
        </div>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" {...handlers}>
        <path d={area((v) => Math.max(v, 0))} fill="rgba(34,197,94,0.25)" />
        <path d={area((v) => Math.min(v, 0))} fill="rgba(239,68,68,0.25)" />

        <line
          x1={PAD}
          y1={H / 2}
          x2={W - PAD}
          y2={H / 2}
          stroke="rgba(255,255,255,0.15)"
        />
        {gold.map(
          (_, i) =>
            i > 0 &&
            i % 10 === 0 && (
              <text
                key={i}
                x={x(i)}
                y={H - 2}
                fontSize="9"
                fill="rgba(156,163,175,0.6)"
                textAnchor="middle"
              >
                {i}m
              </text>
            ),
        )}
        {xp && (
          <polyline
            points={line(xp)}
            fill="none"
            stroke={XP_COLOR}
            strokeWidth="1.5"
          />
        )}
        <polyline
          points={line(gold)}
          fill="none"
          stroke={GOLD_COLOR}
          strokeWidth="1.5"
        />
        {hover != null && (
          <line
            x1={x(hover)}
            y1={PAD}
            x2={x(hover)}
            y2={H - PAD}
            stroke="rgba(255,255,255,0.25)"
          />
        )}
        {showDeaths &&
          stacks.map((s) =>
            s.items.map((d, i) => {
              const href = heroIconUrl(d.heroId);
              if (!href) return null;
              const ix = Math.min(W - PAD - DEATH_W, Math.max(PAD, s.x - DEATH_W / 2));
              const iy = H - 14 - DEATH_H - i * (DEATH_H + 1);
              const mine = me && d.heroId === me.hero_id;
              return (
                <g key={`${s.x}-${i}`} pointerEvents="none">
                  <image
                    href={href}
                    x={ix}
                    y={iy}
                    width={DEATH_W}
                    height={DEATH_H}
                    preserveAspectRatio="xMidYMid slice"
                  />
                  {mine && (
                    <rect
                      x={ix}
                      y={iy}
                      width={DEATH_W}
                      height={DEATH_H}
                      fill="none"
                      stroke={ME_COLOR}
                      strokeWidth="1"
                    />
                  )}
                </g>
              );
            }),
          )}
        {showDeaths &&
          enemyStacks.map((s) =>
            s.items.map((d, i) => {
              const href = heroIconUrl(d.heroId);
              if (!href) return null;
              const ix = Math.min(W - PAD - DEATH_W, Math.max(PAD, s.x - DEATH_W / 2));
              // Top-down under the item row, high enough that a full five-man
              // wipe stays above the zero line.
              const iy = 16 + i * (DEATH_H + 1);
              const ring = d.byMe
                ? KILL_COLOR
                : inMyFight(d.time)
                  ? ASSIST_COLOR
                  : null;
              return (
                <g key={`e${s.x}-${i}`} pointerEvents="none">
                  <image
                    href={href}
                    x={ix}
                    y={iy}
                    width={DEATH_W}
                    height={DEATH_H}
                    preserveAspectRatio="xMidYMid slice"
                  />
                  {ring && (
                    <rect
                      x={ix}
                      y={iy}
                      width={DEATH_W}
                      height={DEATH_H}
                      fill="none"
                      stroke={ring}
                      strokeWidth="1"
                    />
                  )}
                </g>
              );
            }),
          )}
        {showItems &&
          itemStacks.map((s) =>
            s.items.map((b, i) => {
              const ix = Math.min(W - PAD - ITEM_W, Math.max(PAD, s.x - ITEM_W / 2));
              return (
                <image
                  key={`i${s.x}-${i}`}
                  pointerEvents="none"
                  href={`${CDN}/items/${b.short}.png`}
                  x={ix}
                  y={2 + i * (ITEM_H + 1)}
                  width={ITEM_W}
                  height={ITEM_H}
                />
              );
            }),
          )}
        {roshans.map((r, i) => (
          <g key={`rosh-${i}`} pointerEvents="none">
            <title>{`Roshan killed by ${r.radiant ? "Radiant" : "Dire"}, ${Math.round(r.min)}m`}</title>
            <text
              x={x(r.min)}
              y={H / 2 - 6}
              fontSize="10"
              fontWeight="bold"
              textAnchor="middle"
              fill={r.radiant ? "rgba(74,222,128,0.9)" : "rgba(248,113,113,0.9)"}
            >
              R
            </text>
          </g>
        ))}
        <text x={PAD} y={12} fontSize="9" fill="rgba(156,163,175,0.8)">
          +{fmtK(max)}
        </text>
      </svg>
      <Legend
        entries={[
          ["Gold", GOLD_COLOR],
          ...(xp ? [["XP", XP_COLOR]] : []),
          ...(me && showDeaths ? [["Your deaths", ME_COLOR]] : []),
          ...(me && showDeaths && enemyDeaths.length
            ? [
                ["Your kill", KILL_COLOR],
                ["Your assist (same fight)", ASSIST_COLOR],
              ]
            : []),
          ...(roshans.length ? [["R = Roshan (killer's color)", "rgba(156,163,175,0.6)"]] : []),
        ]}
      />
    </div>
  );
}

const FARM_COLORS = ["rgba(129,140,248,0.9)", "rgba(248,113,113,0.9)"];

export function FarmGraphCard({ match, accountId }) {
  const withLH = (side) =>
    match.players.filter((p) => p.isRadiant === side && p.lh_t?.length > 1);
  const topLH = (ps) => [...ps].sort((a, b) => b.last_hits - a.last_hits)[0];
  let me = match.players.find(
    (p) => p.account_id === accountId && p.lh_t?.length > 1,
  );
  if (!me) me = topLH(withLH(true));
  const rival = me ? topLH(withLH(!me.isRadiant)) : null;
  const series = me ? (rival ? [me, rival] : [me]) : [];

  const len = Math.max(0, ...series.map((p) => p.lh_t.length));
  const [hover, handlers] = useHoverIndex(len);
  if (!me) return null;

  const max = Math.max(1, ...series.flatMap((p) => p.lh_t));
  const x = (i) => PAD + (i * (W - 2 * PAD)) / Math.max(1, len - 1);
  const y = (v) => H - PAD - (v / max) * (H - 2 * PAD);
  const line = (p) =>
    p.lh_t.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");

  return (
    <div className={CARD}>
      <div className="flex items-baseline justify-between mb-1.5">
        <div className={CARD_TITLE}>Farm (last hits over time)</div>
        {hover != null && (
          <div className="text-[10px] text-gray-400">
            {hover}m{" "}
            {series
              .map(
                (p) =>
                  `${heroName(p.hero_id)} ${p.lh_t[hover] != null ? fmtK(p.lh_t[hover]) : "-"}`,
              )
              .join(" / ")}
          </div>
        )}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" {...handlers}>
        {[...Array(len).keys()].map(
          (i) =>
            i > 0 &&
            i % 10 === 0 && (
              <text
                key={i}
                x={x(i)}
                y={H - 2}
                fontSize="9"
                fill="rgba(156,163,175,0.6)"
                textAnchor="middle"
              >
                {i}m
              </text>
            ),
        )}
        {series.map((p, si) => (
          <polyline
            key={p.player_slot}
            points={line(p)}
            fill="none"
            stroke={FARM_COLORS[si]}
            strokeWidth="1.5"
          />
        ))}
        {hover != null && (
          <line
            x1={x(hover)}
            y1={PAD}
            x2={x(hover)}
            y2={H - PAD}
            stroke="rgba(255,255,255,0.25)"
          />
        )}
        <text x={PAD} y={12} fontSize="9" fill="rgba(156,163,175,0.8)">
          {fmtK(max)}
        </text>
      </svg>
      <Legend
        entries={series.map((p, si) => [heroName(p.hero_id), FARM_COLORS[si]])}
      />
    </div>
  );
}
