import sys
import os
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# must run before the analyzer imports below
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "analyzer"))
os.chdir(PROJECT_ROOT)

from evaluator import Hero, rank_picks, score_teams
from process_query import generate_response
from match_review import review_match, chat_about_match

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


class ReviewRequest(BaseModel):
    accountId: int | None = None
    matchId: int | None = None


class ChatRequest(BaseModel):
    accountId: int
    matchId: int
    message: str


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
    pos = str(req.pos) if req.pos is not None else ""
    side = req.mySide or ""
    response = generate_response(req.query, team, enemy_team, pick, pos, side)
    return {"response": response}


@app.post("/api/review-match")
def api_review_match(req: ReviewRequest):
    result = review_match(account_id=req.accountId, match_id=req.matchId)
    return {"review": result["review"], "toolTrace": result["tool_trace"]}


@app.post("/api/chat")
def api_chat(req: ChatRequest):
    result = chat_about_match(req.accountId, req.matchId, req.message)
    return {"reply": result["reply"], "toolTrace": result["tool_trace"]}
