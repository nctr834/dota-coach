import requests
import json
# Fetch hero data from OpenDota API
response = requests.get("https://api.opendota.com/api/heroStats")

if response.status_code == 200:
    heroes = response.json()
    print(f"Found {len(heroes)} heroes")
else:
    print(f"Error: HTTP {response.status_code}")

hero_by_id = {hero['id'] : hero for hero in heroes}
hero_by_name = {hero['localized_name'].lower() : hero['id'] for hero in heroes}
with open("heroes.json", "w") as f:
    json.dump(hero_by_id, f)

with open("heroes.json", "r") as f:
    hero = json.load(f)[f'{hero_by_name['anti-mage']}']
print(hero)
