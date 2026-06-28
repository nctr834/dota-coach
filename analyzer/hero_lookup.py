"""Single source of truth for hero id/name lookups.

hero_data.json is keyed by hero id as a string ("1", "2", ...) and each entry's
"id" field is also a string. OpenDota, by contrast, returns hero_id as an int,
so two name maps are exposed: NAME_TO_ID (string ids, matching hero_data) and
the int-keyed ID_TO_NAME for resolving OpenDota responses.
"""

from data_loader import hero_data

# name <-> id, with ids kept as strings to match hero_data's own keys.
NAME_TO_ID: dict[str, str] = {h["displayName"]: h["id"] for h in hero_data.values()}

# int-keyed for resolving OpenDota hero_id (which comes back as an int).
ID_TO_NAME: dict[int, str] = {int(h["id"]): h["displayName"] for h in hero_data.values()}


def name_of(hero_id) -> str:
    """Display name for a hero id (accepts str or int), falling back to the id."""
    return ID_TO_NAME.get(int(hero_id), str(hero_id))
