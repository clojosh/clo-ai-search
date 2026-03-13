import asyncio
import json
import multiprocessing
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from posixpath import basename, splitext
from typing import Any, Dict, List

import av
import questionary
import torch
import yt_dlp
from faster_whisper import WhisperModel
from rich.console import Console
from tqdm import tqdm

from ai_search import AISearch
from tools.azure import Azure

# Initialize Rich console for pretty logging
console = Console()

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
model = WhisperModel(
    "large-v3",
    device=DEVICE,
    compute_type="float16",  # or "int8_float16" if VRAM constrained
)


class Bilibili:
    def __init__(self, azure: Azure):
        self.azure = azure
        self.base_dir = Path(f"data/{azure.brand}/bilibili")
        self.cookie_path = os.path.join(os.getcwd(), self.base_dir, "bilibili_cookies.txt")
        self.video_dir = self.base_dir / "videos"
        self.subtitle_dir = self.base_dir / "subtitles"
        self.transcript_dir = self.base_dir / "transcripts"
        self.metadata_dir = self.base_dir / "metadata"

        # Ensure directories exist
        self.video_dir.mkdir(parents=True, exist_ok=True)
        self.subtitle_dir.mkdir(parents=True, exist_ok=True)
        self.transcript_dir.mkdir(parents=True, exist_ok=True)
        self.metadata_dir.mkdir(parents=True, exist_ok=True)

    def has_audio(self, file_path: str) -> bool:
        try:
            container = av.open(file_path)
            return len(container.streams.audio) > 0
        except Exception:
            return False

    def sanitize_filename(self, name: str) -> str:
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

    def retrieve_video_metadata(self, video_url: str):
        """Fetches and saves metadata for a single video."""
        opts = {
            **self._get_common_opts(),
            "extract_flat": False,
        }

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(video_url, download=False)

            if info.get("entries") is None:
                with open(f"{self.base_dir / info.get('id')}.json", "w", encoding="utf-8") as f:
                    json.dump(info, f, ensure_ascii=False, indent=4)
            else:
                for entry in info.get("entries", []):
                    console.print(f"Title: {entry.get('title')}")
                    console.print(f"ID: {entry.get('id')}")
                    console.print(f"URL: {entry.get('webpage_url')}")
                    console.print(f"Upload Date: {entry.get('upload_date')}")
                    console.print(f"Description: {entry.get('description')}\n")

                    with open(f"{self.base_dir / entry.get('id')}.json", "w", encoding="utf-8") as f:
                        json.dump(info, f, ensure_ascii=False, indent=4)

    # Method 1
    def download_channel_videos(self, channel_url: str):
        ydl_opts = {
            # Limit video height to 480p and merge with best audio
            "format": "bv*[height<=480]+ba/b[height<=480] / best[height<=480]",
            "cookiefile": self.cookie_path,
            "merge_output_format": "mp4",  # Forces the final file into MP4 container
            "noplaylist": False,  # Ensure it downloads the whole channel/playlist
            "ignoreerrors": True,
            "sleep_interval": 2,  # Prevent IP flagging
            "outtmpl": str(self.video_dir / "%(title)s_id_%(id)s.%(ext)s"),
            # "playlistend": 5,
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(channel_url, download=True)

                for entry in info["entries"]:
                    # Handle both single videos and playlists (multi-part)
                    sub_entries = entry.get("entries", [entry])

                    for sub_entry in sub_entries:
                        title = self.sanitize_filename(sub_entry.get("title"))
                        print(f"Saving metadata for {title}")

                        raw_date = sub_entry.get("upload_date", "19700101")
                        formatted_date = datetime.strptime(raw_date, "%Y%m%d").strftime("%Y-%m-%dT00:00:00Z")

                        doc = {
                            "video_id": sub_entry.get("id"),
                            "title": title,
                            "url": sub_entry.get("webpage_url"),
                            "published_at": formatted_date,
                            "description": sub_entry.get("description"),
                        }

                        save_path = self.metadata_dir / f"{title}.json"

                        with open(save_path, "w+", encoding="utf-8") as f:
                            json.dump(doc, f, ensure_ascii=False, indent=4)

        except Exception as e:
            print(e)

    def generate_subtitle(self, video_path: str, output_srt_path: str):
        """
        Generate subtitle for a single video using faster-whisper.
        """

        def format_srt_time(seconds: float) -> str:
            td = timedelta(seconds=seconds)
            total_seconds = int(td.total_seconds())
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            secs = total_seconds % 60
            millis = int((seconds - int(seconds)) * 1000)
            return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"

        video_title = splitext(basename(video_path))[0]
        print(f"\nGenerating subtitles for {video_title}")

        segments, info = model.transcribe(
            os.path.join(video_path),
            language="en",
            task="translate",
            beam_size=5,
            # vad_filter=True,  # Skip silence
            # vad_parameters=dict(min_silence_duration_ms=500),
            log_progress=True,  # safer in batch runs
        )

        srt_content = []
        for i, seg in enumerate(segments, start=1):
            start = format_srt_time(seg.start)
            end = format_srt_time(seg.end)
            text = seg.text.strip()

            srt_content.append(f"{i}\n{start} --> {end}\n{text}\n\n")

        with open(output_srt_path, "w", encoding="utf-8") as f:
            f.write("".join(srt_content))

    # Method 2
    def fetch_channel_videos(self, channel_url: str, limit: int = 200) -> List[Dict[str, Any]]:
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
                "source": "Bilibili",
            }

            filename = f"{self.sanitize_filename(entry['title'])}.json"
            save_path = self.transcript_dir / filename

            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False, indent=4)

            console.print(f"[green]✓ Saved transcript:[/green] {entry.get('title')}")

    def prepare_transcripts(self, file_path: str):
        """
        Prepares transcripts by translating and summarizing them.

        Args:
            file_path (str): The path to the transcript file.

        Returns:
            None
        """
        # Read the transcripts from the file
        with open(file_path, "r", encoding="utf-8") as file:
            transcript = json.load(file)

        # Prepare each transcript

        try:
            if transcript["transcript"] == "" or len(transcript["transcript"]) < 150:
                transcript["summary"] = ""
            else:
                title = self.azure.openai_helper.generate_translation(transcript["title"], target_language="English")
                transcript["title"] = title.replace('"', "")

                summary = self.azure.openai_helper.generate_structured_transcript(title, transcript["transcript"])
                transcript["summary"] = summary

                description = self.azure.openai_helper.generate_translation(transcript["description"], target_language="English")
                transcript["description"] = description
        except Exception as e:
            raise e

        with open(file_path, "w", encoding="utf-8") as file:
            json.dump(transcript, file, ensure_ascii=False, indent=4)

    def upload_transcripts(self, file_path: str):
        with open(file_path, "r", encoding="utf-8") as f:
            transcript = json.load(f)

            if transcript["transcript"] == "":
                return

            if "summary" in transcript:
                if transcript["summary"] == "" or len(transcript["summary"]) < 150:
                    return

            if transcript["video_id"].startswith("_"):
                transcript["video_id"] = "YT" + transcript["video_id"]

            self.azure.search_client.upload_documents(
                {
                    "@search.action": "mergeOrUpload",
                    "article_id": transcript["video_id"],
                    "url": transcript["url"],
                    "title": transcript["title"],
                    "content": transcript["summary"] if "summary" in transcript else transcript["transcript"],
                    "content_description": transcript["description"],
                    "source": "Bilibili",
                    "created_at": transcript["published_at"],
                    "title_vector": self.azure.openai_helper.generate_embeddings(text=transcript["title"]),
                    "content_vector": self.azure.openai_helper.generate_embeddings(text=transcript["summary"]),
                }
            )


if __name__ == "__main__":
    # Configuration
    CLO3D_TARGET_CHANNEL = "https://space.bilibili.com/477753185/upload/video"
    MD_TARGET_CHANNEL = "https://space.bilibili.com/431424487/upload/video"

    stage = questionary.select("Which stage?", choices=["dev", "prod"]).ask()
    brand = questionary.select("Which brand?", choices=["clo3d", "md", "allinone"]).ask()
    task = questionary.select(
        "What task?",
        choices=[
            "Download All Videos (Method 1)",
            "Generate All Subtitles (Method 1)",
            "Correlate Subtitles with Metadata (Method 1)",
            "Get All Transcripts (Method 2)",
            "Prepare All Transcripts",
            "Upload All Transcripts",
            "Retrieve Video Metadata (Method 1)",
            "Prepare Transcript",
            "Upload Transcript",
            "Find & Delete AI Search Documents",
        ],
    ).ask()

    azure = Azure(stage, brand)
    downloader = Bilibili(azure)
    ai_search = AISearch(azure)

    if task == "Download All Videos (Method 1)":
        downloader.download_channel_videos(CLO3D_TARGET_CHANNEL if brand in ["clo3d"] else MD_TARGET_CHANNEL)

    elif task == "Generate All Subtitles (Method 1)":
        for video_file in os.listdir(downloader.video_dir):
            video_path = os.path.join(downloader.video_dir, video_file)

            output_srt_path = os.path.join(downloader.subtitle_dir, os.path.splitext(video_file)[0] + ".srt")
            if os.path.exists(output_srt_path):
                continue  # Skip if subtitle already exists

            if not downloader.has_audio(video_path):
                continue

            downloader.generate_subtitle(video_path, output_srt_path)

    elif task == "Correlate Subtitles with Metadata (Method 1)":
        for files in os.listdir(downloader.metadata_dir):
            with open(os.path.join(downloader.metadata_dir, files), "r", encoding="utf-8") as f:
                metadata = json.load(f)

            title = metadata["title"]
            video_id = metadata["video_id"]

            # Find matching subtitle file
            subtitle_file = next((s for s in os.listdir(downloader.subtitle_dir) if video_id in s), None)

            if subtitle_file:
                with open(os.path.join(downloader.subtitle_dir, subtitle_file), "r", encoding="utf-8") as f:
                    srt_content = f.read()

                transcript = downloader.clean_srt_to_txt(srt_content)

                doc = {
                    "video_id": video_id,
                    "title": title,
                    "url": metadata["url"],
                    "published_at": metadata["published_at"],
                    "description": metadata["description"],
                    "transcript": transcript,
                }

                save_path = downloader.transcript_dir / f"{downloader.sanitize_filename(title)}.json"
                with open(save_path, "w+", encoding="utf-8") as f:
                    json.dump(doc, f, ensure_ascii=False, indent=4)

    elif task == "Get All Transcripts (Method 2)":
        videos = downloader.fetch_channel_videos(CLO3D_TARGET_CHANNEL if brand in ["clo3d"] else MD_TARGET_CHANNEL)

        for video in videos:
            asyncio.run(downloader.process_video(video["id"]))

    elif task == "Prepare All Transcripts":
        files = [os.path.join(downloader.transcript_dir, f) for f in os.listdir(downloader.transcript_dir)]

        with ThreadPoolExecutor(max_workers=10) as executor:
            # executor.map maintains order; for unordered with tqdm, we use list comprehension or submit
            list(tqdm(executor.map(downloader.prepare_transcripts, files), total=len(files), desc="Overall Progress"))

    elif task == "Upload All Transcripts":
        file_paths = [os.path.join(downloader.transcript_dir, f) for f in os.listdir(downloader.transcript_dir)]
        with ThreadPoolExecutor(max_workers=10) as executor:
            list(tqdm(executor.map(downloader.upload_transcripts, file_paths), total=len(file_paths), desc="Uploading"))

    elif task == "Retrieve Video Metadata (Method 1)":
        video_url = questionary.text("Enter the video URL:").ask()
        downloader.retrieve_video_metadata(video_url)

    elif task == "Prepare Transcript (Method 1)":
        file = questionary.select("Which transcript file?", choices=os.listdir(downloader.transcript_dir)).ask()
        downloader.prepare_transcripts((stage, brand, os.path.join(downloader.transcript_dir, file), 0))

    elif task == "Find & Delete AI Search Documents":
        search_fields_options = ["article_id", "url", "title", "content", "content_description"]

        search_field = questionary.select("Search field?", choices=search_fields_options).ask()
        search_text = questionary.text("Search value?").ask()

        documents = ai_search.find_all_ai_search_documents(search_fields=[search_field], search_text=search_text)

        for document in documents:
            print(document["article_id"] + "\n" + document["url"], "\n")

        print(f"\nTotal documents found: {len(documents)}\n")

        if questionary.confirm("Do you want to delete these documents?").ask():
            for document in documents:
                ai_search.delete_ai_search_document(document["article_id"])
