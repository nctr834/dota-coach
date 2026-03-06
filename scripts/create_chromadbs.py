"""Build dual ChromaDB collections: dota_insights (transcripts) + dota_guides (item guides only)."""

import chromadb
import os
import json
import uuid


def create_insights_collection(client):
    """Create dota_insights collection from condensed transcript data."""
    try:
        client.delete_collection("dota_insights")
        print("Deleted existing dota_insights collection")
    except chromadb.errors.NotFoundError:
        pass

    collection = client.get_or_create_collection("dota_insights")
    count = 0

    channels = list(os.listdir("data/transcripts_condensed"))
    for channel in channels:
        channel_path = f"data/transcripts_condensed/{channel}"
        if not os.path.isdir(channel_path):
            continue
        for file in os.listdir(channel_path):
            with open(f"{channel_path}/{file}") as f:
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
                count += 1

    print(f"dota_insights: {count} documents")
    return count


def create_guides_collection(client):
    """Create dota_guides collection from item RAG data (heroes use direct JSON lookup)."""
    try:
        client.delete_collection("dota_guides")
        print("Deleted existing dota_guides collection")
    except chromadb.errors.NotFoundError:
        pass

    collection = client.get_or_create_collection("dota_guides")
    count = 0

    # Load item guides
    with open("data/RAG/RAG_content_items.json") as f:
        item_data = json.load(f)

    for name, data in item_data["items"].items():
        parts = [f"Item: {name}"]
        if data.get("cost"):
            parts.append(f"Cost: {data['cost']}")
        for field in ["provides", "buyWhen", "skipWhen"]:
            if data.get(field):
                parts.append(f"{field}: {data[field]}")

        doc = "\n".join(parts)
        collection.add(
            documents=[doc],
            metadatas=[{"type": "item", "name": name}],
            ids=[f"item_{name}"],
        )
        count += 1

    # Add role staples as separate documents
    for role, desc in item_data.get("roleStaples", {}).items():
        doc = f"Role staples - {role}: {desc}"
        collection.add(
            documents=[doc],
            metadatas=[{"type": "role_staples", "name": role}],
            ids=[f"staple_{role}"],
        )
        count += 1

    print(f"dota_guides: {count} documents")
    return count


def main():
    client = chromadb.PersistentClient(path="data/vectordb")

    insights_count = create_insights_collection(client)
    guides_count = create_guides_collection(client)

    print(f"\nTotal: {insights_count} insights + {guides_count} guides")


if __name__ == "__main__":
    main()
