import scrapetube
import json
from pathlib import Path
from youtube_transcript_api import YouTubeTranscriptApi
import time
from youtube_transcript_api.proxies import WebshareProxyConfig
import os
from dotenv import load_dotenv

load_dotenv()

ytt_api = YouTubeTranscriptApi(
    proxy_config=WebshareProxyConfig(
        proxy_username=os.getenv("WEBSHARE_USERNAME"),
        proxy_password=os.getenv("WEBSHARE_PASSWORD"),
    )
)

channels = {
    "PainDota": "https://www.youtube.com/@PainDota",
    "BSJ": "https://www.youtube.com/@BananaSlamJamma",
    "ZQuixotix": "https://www.youtube.com/@ZQuixotix",
}

for channel_name, url in channels.items():
    output_dir = Path(f"data/transcripts/{channel_name}")
    output_dir.mkdir(parents=True, exist_ok=True)

    videos = []
    for v in list(scrapetube.get_channel(channel_url=url)):
        time.sleep(1)
        badges = v.get("badges", [])
        is_members = any("MEMBERS_ONLY" in str(b) for b in badges)
        if is_members:
            print(f"Members only: {v['title']['runs'][0]['text']}")
            continue
        vid_id = v["videoId"]
        title = v["title"]["runs"][0]["text"]
        output_path = output_dir / f"{vid_id}.json"
        videos.append(vid_id)
        if len(videos) >= 20:
            break

        if output_path.exists():
            continue

        try:
            transcript = ytt_api.fetch(video_id=vid_id)
            text = " ".join([snippet.text for snippet in transcript.snippets])
            output_path.write_text(
                json.dumps(
                    {
                        "video_id": vid_id,
                        "title": title,
                        "channel": channel_name,
                        "transcript": text,
                    }
                )
            )
        except Exception as e:
            print(f"{title}: {e}")
