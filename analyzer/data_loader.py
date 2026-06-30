"""Loads the generated JSON data files in data/, once each, for import by name."""

import json
from pathlib import Path

DATA_DIR = Path("data")


def _load(filename: str, default=None):
    path = DATA_DIR / filename
    if default is not None and not path.exists():
        return default
    with open(path, "r") as f:
        return json.load(f)


hero_data = _load("hero_data.json")
matchup_data = _load("matchup_data.json")
pos_data = _load("pos_data.json")
item_data = _load("item_data.json")
item_displayName_to_id = _load("item_displayName_to_id.json")
patch_data = _load("patch_data.json")
aghs_data = _load("aghs_data.json", default={})
rag_items = _load("RAG/RAG_content_items.json")["items"]

# Built by later pipeline steps; absent on a fresh checkout.
hero_tags = _load("hero_tags.json", default={})
hero_item_builds = _load("hero_item_builds.json", default={})
