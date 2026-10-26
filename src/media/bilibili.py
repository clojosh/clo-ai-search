import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
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

_src_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _src_root not in sys.path:
    sys.path.insert(0, _src_root)

from search.ai_search import AISearch
from tools.azure import Azure

# Initialize Rich console for pretty logging
console = Console()

BILIBILI_BLOCKED_MESSAGE = "Bilibili blocked the request with HTTP 412. Wait before retrying, and refresh data/<brand>/bilibili/bilibili_cookies.txt from a logged-in browser session if it continues."

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
model = WhisperModel(
    "large-v3",
    device=DEVICE,
    compute_type="float16",  # or "int8_float16" if VRAM constrained
)


def prompt_for_date_range() -> tuple[date, date]:
    today = datetime.now().date()
    date_range = questionary.select(
        "Upload date range:",
        choices=[
            "Last 7 days",
            "Last 30 days",
            "Last 90 days",
            "Custom date range",
        ],
    ).ask()

    if date_range == "Last 7 days":
        return today - timedelta(days=7), today
    if date_range == "Last 30 days":
        return today - timedelta(days=30), today
    if date_range == "Last 90 days":
        return today - timedelta(days=90), today

    while True:
        start_date_str = questionary.text("Start date (YYYY-MM-DD):").ask()
        end_date_str = questionary.text("End date (YYYY-MM-DD):", default=today.strftime("%Y-%m-%d")).ask()

        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            print("Please enter dates in YYYY-MM-DD format.")
            continue

        if start_date > end_date:
            print("Start date must be on or before end date.")
            continue

        return start_date, end_date


class Bilibili:
    def __init__(self, azure: Azure):
        self.azure = azure
        self.base_dir = Path(f"data/{azure.brand}/bilibili")
        self.cookie_path = os.path.join(os.getcwd(), self.base_dir, "bilibili_cookies.txt")
        self.alternate_cookie_path = os.path.join(os.getcwd(), self.base_dir, "bilibili.com_cookies.txt")
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

    def _sanitize_search_key(self, value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_=-]", "_", value)

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
        cookie_path = self._get_cookie_path()
        return {
            "cookiefile": cookie_path,
            "extractor_retries": 3,
            "fragment_retries": 3,
            "retries": 3,
            "sleep_interval": 2,
            "quiet": True,
            "no_warnings": True,
        }

    def _get_cookie_path(self) -> str:
        if os.path.exists(self.alternate_cookie_path) and os.path.getsize(self.alternate_cookie_path) > 1000:
            return self.alternate_cookie_path

        if os.path.exists(self.cookie_path) and os.path.getsize(self.cookie_path) <= 500:
            console.print("[yellow]Bilibili cookie file looks like an anonymous yt-dlp cookie jar. Refresh it from a logged-in browser session if requests are blocked.[/yellow]")

        return self.cookie_path

    def _is_bilibili_block(self, error: Exception) -> bool:
        return "412" in str(error) and "Bilibili" in str(error)

    def _video_id_from_filename(self, video_file: str) -> str | None:
        match = re.search(r"_id_([^_]+)(?:_p\d+)?$", Path(video_file).stem)
        return match.group(1) if match else None

    def get_existing_transcript_keys(self) -> set[str]:
        keys = set()

        for transcript_file in self.transcript_dir.glob("*.json"):
            keys.add(transcript_file.stem)

            try:
                with open(transcript_file, "r", encoding="utf-8") as f:
                    transcript = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue

            video_id = transcript.get("video_id")
            if video_id:
                keys.add(video_id)

        return keys

    def has_existing_transcript_for_video(self, video_file: str, transcript_keys: set[str]) -> bool:
        video_stem = Path(video_file).stem
        video_id = self._video_id_from_filename(video_file)

        return video_stem in transcript_keys or bool(video_id and video_id in transcript_keys)

    def _split_markdown_sections(self, text: str) -> list[str]:
        sections = []
        current_section = []

        for line in text.splitlines():
            if re.match(r"^#{1,6}\s+", line) and current_section:
                sections.append("\n".join(current_section).strip())
                current_section = []

            current_section.append(line)

        if current_section:
            sections.append("\n".join(current_section).strip())

        return [section for section in sections if section]

    def _split_long_text(self, text: str, max_words: int = 500, overlap_words: int = 75) -> list[str]:
        if len(text.split()) <= max_words:
            return [text.strip()] if text.strip() else []

        sentences = self._split_sentences(text)
        if len(sentences) <= 1:
            return self._split_long_sentence(text, max_words=max_words, overlap_words=overlap_words)

        chunks = []
        current_sentences = []
        current_word_count = 0

        for sentence in sentences:
            sentence_word_count = len(sentence.split())

            if sentence_word_count > max_words:
                if current_sentences:
                    chunks.append(" ".join(current_sentences).strip())
                    current_sentences = self._sentence_overlap(current_sentences, overlap_words)
                    current_word_count = sum(len(overlap_sentence.split()) for overlap_sentence in current_sentences)

                chunks.extend(self._split_long_sentence(sentence, max_words=max_words, overlap_words=overlap_words))
                current_sentences = []
                current_word_count = 0
                continue

            if current_sentences and current_word_count + sentence_word_count > max_words:
                chunks.append(" ".join(current_sentences).strip())
                current_sentences = self._sentence_overlap(current_sentences, overlap_words)
                current_word_count = sum(len(overlap_sentence.split()) for overlap_sentence in current_sentences)

            current_sentences.append(sentence)
            current_word_count += sentence_word_count

        if current_sentences:
            chunks.append(" ".join(current_sentences).strip())

        return chunks

    def _split_sentences(self, text: str) -> list[str]:
        sentences = re.split(r"(?<=[.!?。！？])\s+(?=[A-Z0-9#*\-\"'(\[])|(?<=[。！？])", text.strip())
        return [sentence.strip() for sentence in sentences if sentence.strip()]

    def _sentence_overlap(self, sentences: list[str], overlap_words: int) -> list[str]:
        if overlap_words <= 0:
            return []

        overlap = []
        word_count = 0
        for sentence in reversed(sentences):
            overlap.insert(0, sentence)
            word_count += len(sentence.split())
            if word_count >= overlap_words:
                break

        return overlap

    def _split_long_sentence(self, text: str, max_words: int = 500, overlap_words: int = 75) -> list[str]:
        words = text.split()
        chunks = []
        start = 0

        while start < len(words):
            end = min(start + max_words, len(words))
            chunks.append(" ".join(words[start:end]).strip())

            if end == len(words):
                break

            start = max(end - overlap_words, start + 1)

        return chunks

    def chunk_markdown_content(self, text: str, max_words: int = 500, overlap_words: int = 75) -> list[str]:
        chunks = []
        pending_section = ""

        for section in self._split_markdown_sections(text):
            section_words = section.split()
            pending_words = pending_section.split()

            if pending_section and len(pending_words) + len(section_words) <= max_words:
                pending_section = f"{pending_section}\n\n{section}"
                continue

            if pending_section:
                chunks.extend(self._split_long_text(pending_section, max_words=max_words, overlap_words=overlap_words))

            pending_section = section

        if pending_section:
            chunks.extend(self._split_long_text(pending_section, max_words=max_words, overlap_words=overlap_words))

        return chunks

    def build_chunked_documents(self, transcript: dict, max_words: int = 500, overlap_words: int = 75) -> list[dict]:
        if transcript["transcript"] == "" or transcript["transcript"] is None:
            print(f"\nTranscript: {transcript.get('title', '')} is missing transcript. Skipping chunking.")
            return []

        markdown_content = transcript.get("markdown_content")
        if markdown_content is None or markdown_content == "" or len(markdown_content) < 150:
            return []

        video_id = transcript["video_id"]
        if video_id.startswith("_"):
            video_id = "YT" + video_id

        chunks = self.chunk_markdown_content(markdown_content, max_words=max_words, overlap_words=overlap_words)
        if not chunks:
            return []

        documents = []
        for i, chunk in enumerate(chunks, start=1):
            documents.append(
                {
                    "article_id": f"{video_id}#chunk-{i}",
                    "parent_id": video_id,
                    "chunk_index": i,
                    "chunk_count": len(chunks),
                    "url": transcript["url"],
                    "title": transcript["title"],
                    "content": chunk,
                    "content_description": transcript["description"],
                    "source": "Bilibili",
                    "created_at": transcript["published_at"],
                }
            )

        return documents

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
            **self._get_common_opts(),
            # Limit video height to 480p and merge with best audio
            "format": "bv*[height<=480]+ba/b[height<=480] / best[height<=480]",
            "merge_output_format": "mp4",  # Forces the final file into MP4 container
            "noplaylist": False,  # Ensure it downloads the whole channel/playlist
            "ignoreerrors": True,
            "outtmpl": str(self.video_dir / "%(title)s_id_%(id)s.%(ext)s"),
            # "playlistend": 5,
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(channel_url, download=True)

                if not info:
                    console.print(f"[bold red]{BILIBILI_BLOCKED_MESSAGE}[/bold red]")
                    return

                for entry in info.get("entries", []):
                    if not entry:
                        continue

                    # Handle both single videos and playlists (multi-part)
                    sub_entries = entry.get("entries", [entry])

                    for sub_entry in sub_entries:
                        if not sub_entry:
                            continue

                        title = self.sanitize_filename(sub_entry.get("title") or sub_entry.get("id") or "untitled")
                        print(f"Saving metadata for {title}")

                        raw_date = sub_entry.get("upload_date") or "19700101"
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
            if self._is_bilibili_block(e):
                console.print(f"[bold red]{BILIBILI_BLOCKED_MESSAGE}[/bold red]")
            else:
                console.print(f"[bold red]Error downloading channel videos:[/bold red] {e}")

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
            try:
                info = ydl.extract_info(channel_url, download=False)
            except Exception as e:
                if self._is_bilibili_block(e):
                    console.print(f"[bold red]{BILIBILI_BLOCKED_MESSAGE}[/bold red]")
                    return []
                raise

            if not info:
                console.print(f"[bold red]{BILIBILI_BLOCKED_MESSAGE}[/bold red]")
                return []

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
        Prepares transcripts by translating their metadata and transcript text.

        Args:
            file_path (str): The path to the transcript file.

        Returns:
            None
        """
        # Read the transcripts from the file
        with open(file_path, "r", encoding="utf-8") as file:
            transcripts = json.load(file)

        def describe_transcript(transcript: dict) -> str:
            title = transcript.get("title") or "Untitled transcript"
            video_id = transcript.get("video_id")
            if video_id:
                return f"{title} ({video_id}) in {file_path}"
            return f"{title} in {file_path}"

        def translate_transcript(transcript: dict) -> None:
            transcript_text = transcript.get("transcript")
            if not transcript_text or len(transcript_text) < 150:
                return

            title = transcript.get("title")
            if title:
                translated_title = self.azure.openai_helper.generate_translation(title, target_language="English")
                transcript["title"] = translated_title.replace('"', "")

            description = transcript.get("description")
            if description:
                transcript["description"] = self.azure.openai_helper.generate_translation(description, target_language="English")

            transcript["transcript"] = self.azure.openai_helper.generate_translation(transcript_text, target_language="English")

        transcript_items = transcripts if isinstance(transcripts, list) else [transcripts]

        for transcript in transcript_items:
            if not isinstance(transcript, dict):
                console.print(f"[yellow]Skipping non-object transcript in {file_path}[/yellow]")
                continue

            try:
                translate_transcript(transcript)
            except Exception as e:
                console.print(f"\n[yellow]Skipping translation for {describe_transcript(transcript)}:[/yellow]")
                console.print(str(e))
                continue

        with open(file_path, "w", encoding="utf-8") as file:
            json.dump(transcripts, file, ensure_ascii=False, indent=4)

    def generate_markdown_content(self, file_path: str):
        """
        Generate markdown content for all transcripts in a file.

        Args:
            file_path (str): The path to the file containing transcripts.

        Returns:
            None
        """
        # Read the transcripts from the file
        with open(file_path, "r", encoding="utf-8") as file:
            transcripts = json.load(file)

        if isinstance(transcripts, dict):
            if transcripts["transcript"] is None:
                return

            try:
                if transcripts["transcript"] == "" or len(transcripts["transcript"]) < 150:
                    transcripts["markdown_content"] = ""
                else:
                    markdown_content = self.azure.openai_helper.generate_structured_transcript(transcripts["title"], transcripts["transcript"])
                    transcripts["markdown_content"] = markdown_content
            except Exception as e:
                print(f"\nError generating markdown content for {file_path}:")
                raise e
        else:
            # Generate markdown content for each transcript.
            for trans in transcripts:
                if trans["transcript"] is None:
                    return

                try:
                    if trans["transcript"] == "" or len(trans["transcript"]) < 150:
                        trans["markdown_content"] = ""
                    else:
                        markdown_content = self.azure.openai_helper.generate_structured_transcript(trans["title"], trans["transcript"])
                        trans["markdown_content"] = markdown_content
                except Exception as e:
                    raise e

        with open(file_path, "w", encoding="utf-8") as file:
            json.dump(transcripts, file, ensure_ascii=False, indent=4)

    def upload_transcript(self, transcript: dict):
        if transcript["transcript"] == "" or transcript["transcript"] is None:
            print(f"\nTranscript: {transcript.get('title', '')} is missing transcript. Skipping upload.")
            return

        video_id = transcript["video_id"]
        if video_id.startswith("_"):
            video_id = "YT" + video_id

        chunks = transcript.get("chunks") or []
        if chunks:
            documents = []
            for chunk in chunks:
                content = chunk.get("content") or chunk.get("markdown_content")
                if not content:
                    continue

                documents.append(
                    {
                        "@search.action": "mergeOrUpload",
                        "article_id": self._sanitize_search_key(
                            chunk.get("article_id") or f"{video_id}_chunk-{chunk.get('chunk_index', len(documents) + 1)}"
                        ),
                        "url": chunk.get("url") or transcript["url"],
                        "title": chunk.get("title") or transcript["title"],
                        "content": content,
                        "content_description": chunk.get("content_description") or transcript["description"],
                        "source": chunk.get("source") or "Bilibili",
                        "created_at": chunk.get("created_at") or transcript["published_at"],
                        "title_vector": self.azure.openai_helper.generate_embeddings(text=chunk.get("title") or transcript["title"]),
                        "content_vector": self.azure.openai_helper.generate_embeddings(text=content),
                    }
                )

            if documents:
                self.azure.search_client.upload_documents(documents)
            return

        markdown_content = transcript.get("markdown_content")
        if markdown_content is not None:
            if markdown_content == "" or len(markdown_content) < 150:
                return

        now = datetime.now(timezone.utc).isoformat()

        # created_at is set once (kept from the existing indexed document if present);
        # updated_at is refreshed on every upload.
        try:
            existing = self.azure.search_client.get_document(key=self._sanitize_search_key(video_id))
            created_at = existing.get("created_at") or now
        except Exception:
            created_at = now

        content = markdown_content if markdown_content is not None else transcript["transcript"]
        self.azure.search_client.upload_documents(
            [
                {
                    "@search.action": "mergeOrUpload",
                    "article_id": self._sanitize_search_key(video_id),
                    "url": transcript["url"],
                    "title": transcript["title"],
                    "content": content,
                    "content_description": transcript["description"],
                    "source": "Bilibili",
                    "article_created_at": transcript["published_at"],
                    "article_updated_at": transcript["published_at"],
                    "created_at": created_at,
                    "updated_at": now,
                    "title_vector": self.azure.openai_helper.generate_embeddings(text=transcript["title"]),
                    "content_vector": self.azure.openai_helper.generate_embeddings(text=content),
                }
            ]
        )

    def upload_transcripts(self, file_path: str, start_date: date | None = None, end_date: date | None = None):
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        transcripts = data if isinstance(data, list) else [data]
        for transcript in transcripts:
            if not isinstance(transcript, dict):
                console.print(f"[yellow]Skipping non-object transcript in {file_path}[/yellow]")
                continue
            raw_publish_date = transcript.get("published_at")
            if start_date is not None and end_date is not None:
                if not raw_publish_date:
                    continue

                publish_date = datetime.strptime(raw_publish_date.split("T")[0], "%Y-%m-%d").date()
                if not start_date <= publish_date <= end_date:
                    continue

            self.upload_transcript(transcript)


if __name__ == "__main__":
    # Configuration
    CLO3D_TARGET_CHANNEL = "https://space.bilibili.com/477753185/upload/video"
    MD_TARGET_CHANNEL = "https://space.bilibili.com/431424487/upload/video"

    app = questionary.select("What do you want to do?", choices=["Chat Bot", "CLO API"]).ask()
    stage = questionary.select("Which stage?", choices=["dev", "prod"]).ask()
    brand = questionary.select("Which brand?", choices=["clo3d", "closet", "connect", "md", "allinone"]).ask()
    task = questionary.select(
        "What task?",
        choices=[
            "Download All Videos (Method 1)",
            "Generate All Subtitles (Method 1)",
            "Correlate Subtitles with Metadata (Method 1)",
            "Get All Transcripts (Alternative to Method 1)",
            "Translate All Transcripts",
            "Generate All Markdown Content",
            "Chunk All Documents",
            "Upload All Transcripts",
            "Chunk Documents",
            "Retrieve Video Metadata",
            "Prepare Transcript",
            "Generate Markdown Content",
            "Upload Transcript",
            "Find & Delete AI Search Documents",
        ],
    ).ask()

    azure = Azure(app, brand, stage)
    downloader = Bilibili(azure)
    ai_search = AISearch(azure)

    if task == "Download All Videos (Method 1)":
        downloader.download_channel_videos(CLO3D_TARGET_CHANNEL if brand in ["clo3d"] else MD_TARGET_CHANNEL)

    elif task == "Generate All Subtitles (Method 1)":
        transcript_keys = downloader.get_existing_transcript_keys()

        for video_file in os.listdir(downloader.video_dir):
            if downloader.has_existing_transcript_for_video(video_file, transcript_keys):
                console.print(f"[yellow]Transcript exists, skipping:[/yellow] {video_file}")
                continue

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
            downloader.process_video(video["id"])

    elif task == "Translate All Transcripts":
        files = [os.path.join(downloader.transcript_dir, f) for f in os.listdir(downloader.transcript_dir) if f.endswith(".json")]

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(downloader.prepare_transcripts, file_path): file_path for file_path in files}
            for future in tqdm(as_completed(futures), total=len(futures), desc="Translating Transcripts", position=0):
                file_path = futures[future]
                try:
                    future.result()
                except Exception as e:
                    console.print(f"\n[yellow]Skipping transcript file {file_path}:[/yellow]")
                    console.print(str(e))

    elif task == "Generate All Markdown Content":
        files = [os.path.join(downloader.transcript_dir, f) for f in os.listdir(downloader.transcript_dir) if f.endswith(".json")]

        with ThreadPoolExecutor(max_workers=10) as executor:
            list(tqdm(executor.map(downloader.generate_markdown_content, files), total=len(files), desc="Generating Markdown Content", position=0))

    elif task == "Generate Markdown Content":
        transcript_files = sorted([file_name for file_name in os.listdir(downloader.transcript_dir) if file_name.endswith(".json")])
        pages = questionary.checkbox("Which pages?", choices=transcript_files).ask()
        for page in pages:
            downloader.generate_markdown_content(os.path.join(downloader.transcript_dir, page))

    elif task in ["Chunk Documents", "Chunk All Documents"]:
        max_words = int(questionary.text("Max words per chunk:", default="500").ask())
        overlap_words = int(questionary.text("Overlap words:", default="75").ask())

        transcript_file_names = sorted([file_name for file_name in os.listdir(downloader.transcript_dir) if file_name.endswith(".json")])

        if task == "Chunk Documents":
            selected_file_names = questionary.checkbox("Which transcript files?", choices=transcript_file_names).ask()
        else:
            selected_file_names = transcript_file_names

        files = [os.path.join(downloader.transcript_dir, file_name) for file_name in selected_file_names]

        def chunk_transcript_file(file_path: str):
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            chunked_count = 0
            transcripts = data if isinstance(data, list) else [data]
            for transcript in transcripts:
                chunks = downloader.build_chunked_documents(
                    transcript,
                    max_words=max_words,
                    overlap_words=overlap_words,
                )
                transcript["chunks"] = chunks
                chunked_count += len(chunks)

            if chunked_count == 0:
                return

            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)

        with ThreadPoolExecutor(max_workers=10) as executor:
            list(tqdm(executor.map(chunk_transcript_file, files), total=len(files), desc="Chunking Documents", position=0))

        print(f"Chunks saved in {len(files)} transcript file(s) under: {downloader.transcript_dir}")

    elif task == "Upload All Transcripts":
        start_date, end_date = prompt_for_date_range()
        file_paths = [os.path.join(downloader.transcript_dir, f) for f in os.listdir(downloader.transcript_dir) if f.endswith(".json")]

        def upload_transcripts(file_path: str):
            downloader.upload_transcripts(file_path, start_date=start_date, end_date=end_date)

        with ThreadPoolExecutor(max_workers=10) as executor:
            list(tqdm(executor.map(upload_transcripts, file_paths), total=len(file_paths), desc="Uploading"))

    elif task == "Retrieve Video Metadata":
        video_url = questionary.text("Enter the video URL:").ask()
        downloader.retrieve_video_metadata(video_url)

    elif task == "Prepare Transcript":
        file = questionary.select("Which transcript file?", choices=os.listdir(downloader.transcript_dir)).ask()
        downloader.prepare_transcripts(os.path.join(downloader.transcript_dir, file))

    elif task == "Find & Delete AI Search Documents":
        search_fields_options = [
            "article_id",
            "url",
            "title",
            "content",
            "content_description",
            "article_created_at",
            "article_updated_at",
            "created_at",
            "updated_at",
        ]

        search_field = questionary.select("Search field?", choices=search_fields_options).ask()
        search_text = questionary.text("Search value?").ask()

        documents = ai_search.find_all_ai_search_documents(search_fields=[search_field], search_text=search_text)

        for document in documents:
            print(document["article_id"] + "\n" + document["url"], "\n")

        print(f"\nTotal documents found: {len(documents)}\n")

        if questionary.confirm("Do you want to delete these documents?").ask():
            for document in documents:
                ai_search.delete_ai_search_document(document["article_id"])
