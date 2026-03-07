"""Add patchNotes field to RAG_content_heroes.json and RAG_content_items.json from patch_notes.json."""

import json

with open("data/patch_notes.json", "r") as f:
    patch_notes = json.load(f)

# --- Heroes ---
with open("data/RAG/RAG_content_heroes.json", "r") as f:
    heroes = json.load(f)

hero_updates = 0
for name, patches in patch_notes["heroes"].items():
    if name not in heroes:
        print(f"  Hero not in RAG: {name}")
        continue
    parts = []
    for patch, changes in patches.items():
        if changes:
            parts.append(f"{patch}: {'; '.join(changes)}")
    if parts:
        heroes[name]["patchNotes"] = " | ".join(parts)
        hero_updates += 1

with open("data/RAG/RAG_content_heroes.json", "w") as f:
    json.dump(heroes, f, indent=2, ensure_ascii=False)

print(f"Heroes updated: {hero_updates}/{len(patch_notes['heroes'])}")

# --- Items ---
with open("data/RAG/RAG_content_items.json", "r") as f:
    items_file = json.load(f)

items = items_file["items"]
item_updates = 0
item_misses = []
for name, patches in patch_notes["items"].items():
    if name not in items:
        item_misses.append(name)
        continue
    parts = []
    for patch, changes in patches.items():
        if changes:
            parts.append(f"{patch}: {'; '.join(changes)}")
    if parts:
        items[name]["patchNotes"] = " | ".join(parts)
        item_updates += 1

with open("data/RAG/RAG_content_items.json", "w") as f:
    json.dump(items_file, f, indent=2, ensure_ascii=False)

print(f"Items updated: {item_updates}/{len(patch_notes['items'])}")
if item_misses:
    print(f"Items not in RAG ({len(item_misses)}): {', '.join(item_misses)}")
