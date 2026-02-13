import asyncio
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import yt_dlp
from rich.console import Console

# Initialize Rich console for pretty logging
console = Console()


class BilibiliDownloader:
    """Handles downloading and processing of Bilibili subtitles and transcripts."""

    def __init__(self, cookie_path: str = "bilibili.com_cookies.txt"):
        self.cookie_path = cookie_path
        self.base_dir = Path("data/md/bilibili")
        self.subtitle_dir = self.base_dir / "subtitles"
        self.transcript_dir = self.base_dir / "transcripts"

        # Ensure directories exist
        self.subtitle_dir.mkdir(parents=True, exist_ok=True)
        self.transcript_dir.mkdir(parents=True, exist_ok=True)

    def sanitize_path(self, name: str) -> str:
        """Removes illegal characters from filenames."""
        return re.sub(r'[\\/*?:"<>|]', "", name)

    def clean_srt_to_txt(self, content: str) -> str:
        """Strips SRT formatting to return raw text transcript."""
        # Remove timestamps
        text = re.sub(r"\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}", "", content)
        # Remove line numbers
        text = re.sub(r"^\d+$", "", text, flags=re.MULTILINE)
        # Remove HTML tags
        text = re.sub(r"<[^>]*>", "", text)
        # Clean up whitespace
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return "\n".join(lines)

    def _get_common_opts(self) -> Dict[str, Any]:
        """Returns base yt-dlp configuration."""
        return {
            "cookiefile": self.cookie_path,
            "quiet": True,
            "no_warnings": True,
        }

    def fetch_channel_videos(self, channel_url: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Retrieves a list of video entries from a channel."""
        opts = {
            **self._get_common_opts(),
            "extract_flat": True,
            "playlistend": limit,
        }

        with yt_dlp.YoutubeDL(opts) as ydl:
            console.print(f"[bold blue]Fetching channel info:[/bold blue] {channel_url}")
            info = ydl.extract_info(channel_url, download=False)
            return info.get("entries", [])

    def process_video(self, video_id: str):
        """Downloads subtitles and saves a formatted transcript JSON."""
        url = f"https://www.bilibili.com/video/{video_id}"

        ydl_opts = {
            **self._get_common_opts(),
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["ai-en.*", "en.*"],
            "skip_download": True,
            "outtmpl": str(self.subtitle_dir / "%(title)s.%(ext)s"),
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)

                # Handle both single videos and playlists (multi-part)
                entries = info.get("entries", [info])

                for entry in entries:
                    self._save_transcript(entry)

        except Exception as e:
            console.print(f"[bold red]Error processing {video_id}:[/bold red] {e}")

    def _save_transcript(self, entry: Dict[str, Any]):
        """Helper to extract subtitle data and save to JSON."""
        subs = entry.get("requested_subtitles")
        if not subs:
            console.print(f"[yellow]No subtitles found for:[/yellow] {entry.get('title')}")
            return

        # Try to find an English subtitle key
        en_key = next((k for k in subs.keys() if "en" in k), None)

        if en_key and "data" in subs[en_key]:
            transcript = self.clean_srt_to_txt(subs[en_key]["data"])

            # Format date: 20231231 -> 2023-12-31T00:00:00Z
            raw_date = entry.get("upload_date", "19700101")
            formatted_date = datetime.strptime(raw_date, "%Y%m%d").strftime("%Y-%m-%dT00:00:00Z")

            doc = {"video_id": entry.get("id"), "title": entry.get("title"), "url": entry.get("webpage_url"), "published_at": formatted_date, "transcript": transcript}

            filename = f"{self.sanitize_path(entry['title'])}.json"
            save_path = self.transcript_dir / filename

            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False, indent=4)

            console.print(f"[green]✓ Saved transcript:[/green] {entry.get('title')}")

    async def run_scaper(self, channel_url: str):
        """Main entry point for the scraper."""
        videos = self.fetch_channel_videos(channel_url)
        console.print(f"Found [bold]{len(videos)}[/bold] videos. Processing...\n")

        # yt-dlp is blocking, so we run it in a thread pool to avoid freezing the loop
        loop = asyncio.get_event_loop()
        for video in videos:
            await loop.run_in_executor(None, self.process_video, video["id"])


if __name__ == "__main__":
    # Configuration
    TARGET_CHANNEL = "https://space.bilibili.com/431424487/video"

    downloader = BilibiliDownloader()
    asyncio.run(downloader.run_scaper(TARGET_CHANNEL))
