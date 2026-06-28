"""Hero id/name lookups."""

from data_loader import hero_data

NAME_TO_ID: dict[str, str] = {h["displayName"]: h["id"] for h in hero_data.values()}

# int-keyed to match OpenDota's hero_id, which is an int.
ID_TO_NAME: dict[int, str] = {int(h["id"]): h["displayName"] for h in hero_data.values()}


def name_of(hero_id) -> str:
    """Display name for a hero id (accepts str or int), falling back to the id."""
    return ID_TO_NAME.get(int(hero_id), str(hero_id))
