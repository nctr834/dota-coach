from chromadb import Client
from composition import compose
import json
import anthropic
import os
from utils import get_dota2protracker_page
from rapidfuzz import process, fuzz
from json_repair import repair_json
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

with open("data/hero_data.json", "r") as f:
    hero_data = json.load(f)
with open("data/item_data.json", "r") as f:
    item_data = json.load(f)

HERO_NAMES = {hero["displayName"]: hero["shortName"] for hero in hero_data.values()}
ITEM_NAMES = {item for item in item_data.keys()}

radiant, dire, radiant_score, dire_score, delta = compose()
user_query = "How should I play clinkz against pl this game? I'm not sure how to itemize or who I should prioritize in fights. I'm thinking of getting Mjollnir at some point but idk."


def _get_response(prompt: str) -> str:
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=200,
        messages=[
            {
                "role": "user",
                "content": prompt,
            }
        ],
    )
    try:
        json_response = json.loads(repair_json(response.content[0].text.strip()))
        return json_response
    except:
        print("Failed to parse response as JSON")
        return []


def _verify_names(names: list[str], valid_names: set[str]) -> list[str]:
    matches = []
    for name in names:
        name = name.title()
        match = process.extractOne(
            name,
            list(valid_names),
            scorer=fuzz.ratio,
            score_cutoff=70,
        )
        if match:
            matches.append(match[0])
    return matches


def _dedup(values: list[str]) -> list[str]:
    seen = set()
    unique_results = []
    for value in values:
        if value not in seen:
            seen.add(value)
            unique_results.append(value)
    return unique_results


def _extract_hero_names(query: str) -> list[str]:
    prompt = f"Extract all hero names from this Dota 2 query. Return only a JSON array of hero names; nothing else. Query: {query}"
    hero_names = _get_response(prompt)
    matches = _verify_names(hero_names, HERO_NAMES.keys())
    return _dedup(matches)


def _extract_item_names(query: str) -> list[str]:
    prompt = f"Extract all item names from this Dota 2 query. Return only a JSON array of item names; nothing else. Query: {query}"
    item_names = _get_response(prompt)
    matches = _verify_names(item_names, ITEM_NAMES)
    return _dedup(matches)


def _is_team_composition_beneficial():
    prompt = f"Would the user benefit from analysis of team compositions? Return only a JSON object with a boolean field 'benefit' set to true or false. Query: {user_query}"
    benefit = _get_response(prompt)
    return benefit.get("benefit") == True


def _is_build_beneficial():
    prompt = f"Would the user benefit from analysis of item/ability builds? Return only a JSON object with a boolean field 'benefit' set to true or false. Query: {user_query}"
    benefit = _get_response(prompt)
    return benefit.get("benefit") == True


if _is_team_composition_beneficial():
    print("Team context:")
    print(team_context)


def gather_context(query):

    if _is_build_beneficial():
        print("Build context:")
        print(build_context)


notes = """
Notes:
- Ability attribute values of 0 may indicate a dynamic or computed value rather than a true zero (e.g. values that scale with other stats at runtime).
- Ability attributes containing 'scepter' or 'shard' in the name with empty values indicate that upgrade exists for the ability; actual upgrade values are unavailable from this data source.
"""

# TODO: extract content from user query to guide retrieval for prompt...

print(notes)
# client = Client(path="data/vectordb")
# collection = client.get_or_create_collection("dota_insights")
# context = collection.query(
#     query_texts=[user_query],
#     n_results=5,
#     include=["metadatas", "documents"],
# )
# query_context = f"""

# Radiant: {radiant}
# Dire: {dire}
# Radiant score: {radiant_score}
# Dire score: {dire_score}
# Delta: {delta}
# VectorDB context: {context}

# """
