import sys
import os
from pathlib import Path

# Ensure analyzer/ is importable (evaluator does `from counters import ...`)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "analyzer"))
os.chdir(PROJECT_ROOT)

import json
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from evaluator import Hero, rank_picks, score_teams
from process_query import generate_response

with open(PROJECT_ROOT / "data/hero_data.json") as f:
    hero_data = json.load(f)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class HeroPick(BaseModel):
    displayName: str
    id: str
    score: int
    pos: int


class ScoreRequest(BaseModel):
    radiant: list[HeroPick]
    dire: list[HeroPick]


class RankRequest(BaseModel):
    team: list[HeroPick]
    enemyTeam: list[HeroPick]
    pos: int


class QueryRequest(BaseModel):
    query: str
    team: list[HeroPick]
    enemyTeam: list[HeroPick]
    pos: int | None = None
    pick: str | None = None
    mySide: str | None = None


def picks_to_dict(picks: list[HeroPick]) -> dict:
    result = {}
    for p in picks:
        result[p.pos] = Hero(p.displayName, p.id, 0, str(p.pos))
    return result


@app.post("/api/score-teams")
def api_score_teams(req: ScoreRequest):
    radiant = picks_to_dict(req.radiant)
    dire = picks_to_dict(req.dire)
    radiant_score, dire_score, delta = score_teams(radiant, dire)
    return {
        "radiantScore": round(radiant_score, 2),
        "direScore": round(dire_score, 2),
        "delta": round(delta, 2),
    }


@app.post("/api/rank-picks")
def api_rank_picks(req: RankRequest):
    team = picks_to_dict(req.team)
    enemy_team = picks_to_dict(req.enemyTeam)
    pos_key = str(req.pos)
    picks = rank_picks(team, enemy_team, pos_key)
    return {
        "picks": [
            {"displayName": p.name, "id": p.id, "score": p.score, "pos": p.pos}
            for p in picks
        ]
    }


@app.post("/api/query")
def api_query(req: QueryRequest):
    team = picks_to_dict(req.team)
    enemy_team = picks_to_dict(req.enemyTeam)
    pick = req.pick or ""
    pos = f"{req.pos}" if req.pos else ""
    side = req.mySide or ""
    response = generate_response(req.query, team, enemy_team, pick, pos, side)
    return {"response": response}
