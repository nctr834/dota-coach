import anthropic
from json_repair import repair_json
import os
from pathlib import Path
import json
from dotenv import load_dotenv

load_dotenv()
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
prompt = """Extract all actionable Dota 2 insights from this transcript. Output ONLY a JSON array, no other text.

For each insight, use this schema:
{{
  "hero": string or null,
  "role": string or null,
  "topic": string,
  "matchup_context": string or null,
  "insight": string,
}}

Example output:
[
  {{
    "hero": "Morphling",
    "role": "carry",
    "topic": "farming_pattern",
    "matchup_context": "against heavy gank lineups",
    "insight": "Prioritize Waveform to farm dangerous areas of the map. Shift strength before moving to triangle to survive ganks.",
  }},
  {{
    "hero": null,
    "role": "support",
    "topic": "lane_trading",
    "matchup_context": null,
    "insight": "Trade HP with the enemy support when your carry is going for last hits. Force the enemy to choose between harassing your carry or trading back with you.",
  }}
]

Rules:
- Ignore filler, repetition, self-promotion, and off-topic tangents
- Keep insights concise, specific and actionable, not vague platitudes
- If the advice is general to a role rather than a specific hero, set hero to null
- Correct common transcript mistranslations of Dota 2 terms (e.g., "Muelir" → "Mjollnir", "BKB" → "Black King Bar", etc.)

TRANSCRIPT:
"""


def condense_transcript(channel_name, prompt):
    transcripts_dir = Path(f"data/transcripts_raw/{channel_name}")
    output_dir = Path(f"data/transcripts_condensed/{channel_name}")
    output_dir.mkdir(parents=True, exist_ok=True)
    for transcript_path in transcripts_dir.glob("*.json"):
        output_path = output_dir / f"{transcript_path.name}"
        if output_path.exists():
            continue
        with open(transcript_path, "r") as f:
            transcript = json.load(f)
            message = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=4096,
                messages=[
                    {"role": "user", "content": prompt + transcript["transcript"]}
                ],
            )
            response_text = message.content[0].text
            try:
                insights = json.loads(response_text)
            except json.JSONDecodeError:
                cleaned = (
                    response_text.strip()
                    .removeprefix("```json")
                    .removesuffix("```")
                    .strip()
                )
                insights = json.loads(repair_json(cleaned))
            for insight in insights:
                insight["source"] = {
                    "video_id": transcript["video_id"],
                    "title": transcript["title"],
                    "channel": channel_name,
                }
            with open(output_path, "w") as f:
                json.dump(insights, f, indent=2)


if __name__ == "__main__":
    channels = ["PainDota", "BSJ", "ZQuixotix"]
    for channel in channels:
        condense_transcript(channel, prompt)
