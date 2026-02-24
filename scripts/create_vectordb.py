import chromadb
import os
import json
import uuid

client = chromadb.PersistentClient(path="data/vectordb")
try:
    client.delete_collection("dota_insights")
    print("Deleting existing collection")
except chromadb.errors.NotFoundError:
    print("No existing collection")

collection = client.get_or_create_collection("dota_insights")
channels = list(os.listdir("data/transcripts_condensed"))
for channel in channels:
    for file in os.listdir(f"data/transcripts_condensed/{channel}"):
        with open(f"data/transcripts_condensed/{channel}/{file}") as f:
            transcript = json.load(f)
        for insight in transcript:
            if "insight" not in insight:
                continue
            collection.add(
                documents=[insight["insight"]],
                metadatas=[
                    {
                        k: v
                        for k, v in {
                            "hero": insight.get("hero"),
                            "role": insight.get("role"),
                            "topic": insight.get("topic"),
                            "matchup_context": insight.get("matchup_context"),
                            "video_id": insight.get("video_id"),
                            "title": insight.get("title"),
                            "channel": insight.get("channel"),
                        }.items()
                        if v is not None
                    }
                ],
                ids=[str(uuid.uuid4())],
            )
print("Done")
