from chromadb import Client
from composition import compose
import json

with open("data/hero_data.json", "r") as f:
    hero_data = json.load(f)

radiant, dire, radiant_score, dire_score, delta = compose()
user_query = "How should I play clinkz against phantom lancer this game? I'm not sure how to itemize or who I should prioritize in fights."

hero_context = ""
for hero in radiant.values():
    hero_context += f"{hero.name}: {hero_data[str(hero.id)]}\n"
for hero in dire.values():
    hero_context += f"{hero.name}: {hero_data[str(hero.id)]}\n"
print(hero_context)

notes = """
Notes:
- Attributes in abilities that have shard or scepter in the name have empty fields. If the field is empty, it signifies the ability/attribute is upgraded. API does not retrieve the values for some reason.
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
