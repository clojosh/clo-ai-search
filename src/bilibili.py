import asyncio
import json
import multiprocessing
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import questionary
import yt_dlp
from rich.console import Console
from tqdm import tqdm

from ai_search import AISearch
from tools.azure import Azure

# Initialize Rich console for pretty logging
console = Console()


class Bilibili:
    def __init__(self, azure: Azure, cookie_path: str = "bilibili.com_cookies.txt"):
        self.azure = azure
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

            doc = {
                "video_id": entry.get("id"),
                "title": entry.get("title"),
                "url": entry.get("webpage_url"),
                "published_at": formatted_date,
                "description": entry.get("description"),
                "transcript": transcript,
            }

            filename = f"{self.sanitize_path(entry['title'])}.json"
            save_path = self.transcript_dir / filename

            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False, indent=4)

            console.print(f"[green]✓ Saved transcript:[/green] {entry.get('title')}")

    @staticmethod
    def prepare_transcripts(params):
        """
        Prepares transcripts by translating and summarizing them.

        Args:
            params (tuple): A tuple containing the stage, brand, file path, and worker id.

        Returns:
            None
        """
        azure, file_path = params

        # Read the transcripts from the file
        with open(file_path, "r", encoding="utf-8") as file:
            transcript = json.load(file)

        # Prepare each transcript

        try:
            if transcript["transcript"] == "" or len(transcript["transcript"]) < 150:
                transcript["summary"] = ""
            else:
                title = azure.openai_helper.generate_translation(transcript["title"], target_language="English")
                transcript["title"] = title.replace('"', "")

                summary = azure.openai_helper.generate_structured_transcript(title, transcript["transcript"])
                transcript["summary"] = summary

                description = azure.openai_helper.generate_translation(transcript["description"], target_language="English")
                transcript["description"] = description
        except Exception as e:
            raise e

        with open(file_path, "w", encoding="utf-8") as file:
            json.dump(transcript, file, ensure_ascii=False, indent=4)

    def mp_prepare_transcripts(self):
        files = os.listdir(self.transcript_dir)
        num_workers = 4  # Adjust based on your API limits

        # Pass the worker index (i % num_workers) so they don't fight for the same line
        prepare_transcripts_params = [(self.azure, os.path.join(self.transcript_dir, f)) for f in files]
        # Main progress bar (Position 0)
        with multiprocessing.Pool(num_workers) as p:
            for _ in tqdm(p.imap_unordered(Bilibili.prepare_transcripts, prepare_transcripts_params), total=len(prepare_transcripts_params), desc="Overall Progress", position=0):
                pass

    def upload_transcripts(self):
        for file in tqdm(os.listdir(os.path.join(self.transcript_dir)), desc="Uploading Transcripts", colour="green", position=0, leave=True):
            with open(os.path.join(self.transcript_dir, file), "r", encoding="utf-8") as f:
                transcript = json.load(f)

                if transcript["transcript"] == "":
                    continue

                if "summary" in transcript:
                    if transcript["summary"] == "" or len(transcript["summary"]) < 150:
                        continue

                if transcript["video_id"].startswith("_"):
                    transcript["video_id"] = "YT" + transcript["video_id"]

                self.azure.search_client.upload_documents(
                    {
                        "@search.action": "mergeOrUpload",
                        "article_id": transcript["video_id"],
                        "source": transcript["url"],
                        "title": transcript["title"],
                        "content": transcript["summary"] if "summary" in transcript else transcript["transcript"],
                        "content_description": transcript["description"],
                        "created_at": transcript["published_at"],
                        "youtube_links": [transcript["url"]],
                        "title_vector": self.azure.openai_helper.generate_embeddings(text=transcript["title"]),
                        "content_vector": self.azure.openai_helper.generate_embeddings(text=transcript["summary"]),
                    }
                )

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

    stage = questionary.select("Which stage?", choices=["dev", "prod"]).ask()
    brand = questionary.select("Which brand?", choices=["clo3d", "md", "allinone"]).ask()
    task = questionary.select(
        "What task?",
        choices=[
            "Get All Transcripts",
            "Prepare All Transcripts",
            "Upload All Transcripts",
            "Prepare Transcript",
            "Upload Transcript",
            "Find & Delete AI Search Documents",
        ],
    ).ask()

    azure = Azure(stage, brand)
    downloader = Bilibili(azure)
    ai_search = AISearch(azure)

    if task == "Get All Transcripts":
        asyncio.run(downloader.run_scaper(TARGET_CHANNEL))

    elif task == "Prepare All Transcripts":
        downloader.mp_prepare_transcripts()

    elif task == "Upload All Transcripts":
        downloader.upload_transcripts()

    elif task == "Prepare Transcript":
        file = questionary.select("Which transcript file?", choices=os.listdir(downloader.transcript_dir)).ask()
        downloader.prepare_transcripts((stage, brand, os.path.join(downloader.transcript_dir, file), 0))

    elif task == "Find & Delete AI Search Documents":
        search_fields_options = ["article_id", "source", "title", "content", "content_description"]

        search_field = questionary.select("Search field?", choices=search_fields_options).ask()
        search_text = questionary.text("Search value?").ask()

        documents = ai_search.find_all_ai_search_documents(search_fields=[search_field], search_text=search_text)

        for document in documents:
            print(document["article_id"] + "\n" + document["source"], "\n")

        print(f"\nTotal documents found: {len(documents)}\n")

        if questionary.confirm("Do you want to delete these documents?").ask():
            for document in documents:
                ai_search.delete_ai_search_document(document["article_id"])
