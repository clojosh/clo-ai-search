import json
import os
import sys
from datetime import datetime
from pathlib import Path

import shortuuid
import yt_dlp

_src_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _src_root not in sys.path:
    sys.path.insert(0, _src_root)

from src.media.youtube.api import YouTubeAPI
from src.media.youtube.constants import EXCLUDED_LANGS_AND_WORDS
from src.media.youtube.yt_dlp_options import print_youtube_block_hint, youtube_yt_dlp_options


class VideoManager:
    def __init__(self, azure, youtube_channel_dir_path):
        self.azure = azure
        self.youtube_channel_dir_path = youtube_channel_dir_path

    def _should_skip_existing_transcript(self, transcript_path: str) -> bool:
        if not os.path.exists(transcript_path):
            return False

        try:
            with open(transcript_path, "r", encoding="utf-8") as f:
                transcript_data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return False

        if not isinstance(transcript_data, dict):
            return False

        return transcript_data.get("transcript") is not None

    def download_videos_yt_dlp(self, starting_video_index: int = 0, num_recent_videos: int = 10):
        """Downloads only standard published videos (no Shorts, no Live)."""

        # Using /videos ensures we start on the main uploads tab
        CHANNEL_URL = "https://www.youtube.com/@CLO3D/videos" if self.azure.brand == "clo3d" else "https://www.youtube.com/@MarvelousDesigner/videos"
        OUTPUT_DIR = os.path.join(self.youtube_channel_dir_path, "videos")

        if not os.path.exists(OUTPUT_DIR):
            os.makedirs(OUTPUT_DIR)

        # --- Comprehensive Filter ---
        def published_videos_only(info_dict, *, incomplete):
            """
            Filters out:
            1. Live streams and upcoming premieres.
            2. Shorts (defined as videos 60 seconds or shorter).
            """
            # Check for Live/Upcoming
            if info_dict.get("is_live") or info_dict.get("live_status") == "is_upcoming":
                return "Skipping: Live or Upcoming"

            # Check for Shorts (Duration <= 60s and usually vertical)
            # We use 61 to be safe with rounding
            duration = info_dict.get("duration")
            if duration and duration <= 61:
                return "Skipping: Short (Duration <= 60s)"

            return None

        ydl_opts = {
            **youtube_yt_dlp_options(),
            "format": "bestvideo[height<=480]+bestaudio/best[height<=480]",
            "playlist_items": f"{starting_video_index}-{starting_video_index + num_recent_videos - 1}",
            "outtmpl": os.path.join(OUTPUT_DIR, "%(title)s [%(id)s]", "%(title)s.%(ext)s"),
            "match_filter": published_videos_only,
            "restrictfilenames": True,
            "writedescription": True,
            "writeinfojson": True,
            "embed_metadata": True,
            "embed_thumbnail": True,
            "ignoreerrors": True,
            "quiet": False,
        }

        print(f"Searching for {num_recent_videos} videos...")

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([CHANNEL_URL])
            print("\nDownload process complete!")
        except Exception as e:
            print_youtube_block_hint(e)

    def prepare_transcripts_from_dl_videos(self, video_dir_path: str):
        """
        Prepare the transcripts from the downloaded videos by extracting the descriptions and generating the transcripts using faster-whisper.
        """

        video_dir_path = Path(video_dir_path)

        print(f"\nPreparing transcript for {video_dir_path.stem}")

        for file in os.listdir(video_dir_path):
            if any(lang.lower() in file.lower() for lang in EXCLUDED_LANGS_AND_WORDS):
                continue

            if file.endswith(".info.json"):
                transcript_path = os.path.join(self.youtube_channel_dir_path, "transcripts", file.replace(".info.json", ".json"))
                if self._should_skip_existing_transcript(transcript_path):
                    continue

                with open(os.path.join(video_dir_path, file), "r", encoding="utf-8") as f:
                    description_data = json.load(f)

                description = description_data.get("description", "")

                youtube_api = YouTubeAPI(self.azure, self.youtube_channel_dir_path)
                transcript = youtube_api.extract_srt_text(os.path.join(video_dir_path, file.replace(".info.json", ".srt")))

                upload_date = description_data.get("upload_date")
                formatted_date = datetime.strptime(upload_date, "%Y%m%d").strftime("%Y-%m-%dT%H:%M:%SZ") if upload_date else ""

                doc = {
                    "video_id": description_data.get("id", shortuuid.uuid()),
                    "url": f"https://www.youtube.com/watch?v={description_data.get('id', '')}",
                    "title": description_data.get("title", "").title().replace("&#39;", "'").replace("&quot;", '"').replace("&amp;", "&"),
                    "description": description,
                    "transcript": transcript,
                    "published_at": formatted_date,
                }

                with open(transcript_path, "w+", encoding="utf-8") as f:
                    json.dump(doc, f, ensure_ascii=False, indent=4)
