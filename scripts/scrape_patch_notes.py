from bs4 import BeautifulSoup
import requests
import json
import time
from rapidfuzz import process, fuzz

with open("data/hero_data.json", "r") as f:
    hero_data = json.load(f)

with open("data/item_data.json", "r") as f:
    item_data = json.load(f)

url = "https://liquipedia.net/dota2/api.php"
patches = [
    "7.39",
    "7.39b",
    "7.39c",
    "7.39d",
    "7.39e",
    "7.40",
    "7.40b",
    "7.40c",
]
HERO_NAMES = {hero["displayName"] for hero in hero_data.values()}
ITEM_NAMES = set(item_data.keys())
headers = {"User-Agent": "DotaCoachBot/1.0 (nctr834@gmail.com)"}

heroes = {name: {patch: [] for patch in patches} for name in HERO_NAMES}
items = {name: {patch: [] for patch in patches} for name in ITEM_NAMES}


def parse_patch_page(html: str) -> dict:
    """Parse patch page HTML into {name: [changes]}."""
    soup = BeautifulSoup(html, "html.parser")
    for img in soup.find_all("img"):
        img.decompose()

    results = {}
    current_section = None

    for tag in soup.find_all(["h3", "ul"]):
        if tag.name == "h3":
            section_name = tag.get_text(strip=True)
            if section_name in ("Contents", "Patch Notes", "Additional Content"):
                current_section = None
            else:
                current_section = section_name
            continue

        if not current_section:
            continue

        prev = tag.find_previous_sibling()
        if not prev or prev.name != "div":
            continue

        name = prev.get_text(strip=True)
        if not name:
            continue

        changes = []
        for li in tag.find_all("li", recursive=False):
            text = li.get_text(separator=" ", strip=True)
            if text:
                changes.append(text)

        if changes:
            if name in results:
                results[name].extend(changes)
            else:
                results[name] = changes

    return results


def _verify(name: str, names: set):
    if name not in names:
        match = process.extractOne(
            name,
            names,
            scorer=fuzz.partial_ratio,
            score_cutoff=60,
        )
        if match:
            return match[0]
    return name


for patch in patches:
    params = {
        "action": "parse",
        "page": f"Version_{patch}",
        "prop": "text",
        "format": "json",
    }

    resp = requests.get(url, params=params, headers=headers)
    if resp.status_code != 200:
        print(f"  FAILED: HTTP {resp.status_code}, skipping")
        time.sleep(3)
        continue

    try:
        html = resp.json()["parse"]["text"]["*"]
    except (KeyError, requests.exceptions.JSONDecodeError) as e:
        print(f"  FAILED: {e}, skipping")
        time.sleep(3)
        continue

    results = parse_patch_page(html)
    hero_count = 0
    item_count = 0

    for name, changes in results.items():
        if name in HERO_NAMES:
            heroes[name][patch] = changes
            hero_count += 1
        else:
            name = _verify(name, ITEM_NAMES)
        if name in ITEM_NAMES:
            items[name][patch] = changes
            item_count += 1

    print(f"{patch}: {hero_count} heroes, {item_count} items")
    time.sleep(2)

# Remove heroes/items with no changes across any patch
heroes = {
    name: patches_data
    for name, patches_data in heroes.items()
    if any(changes for changes in patches_data.values())
}
items = {
    name: patches_data
    for name, patches_data in items.items()
    if any(changes for changes in patches_data.values())
}

output = {"heroes": heroes, "items": items}

with open("data/patch_notes.json", "w") as f:
    json.dump(output, f, indent=2, ensure_ascii=False)

print(f"\nTotal: {len(heroes)} heroes, {len(items)} items with changes")
