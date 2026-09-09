#!/usr/bin/env python3
"""
Main YouTube module that orchestrates all YouTube-related functionality.
This file contains the main entry point and orchestration logic for YouTube processing tasks.
"""

import json
import os
import re
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

import questionary
from tqdm import tqdm

# Running as `python media/youtube.py` puts `media/` on sys.path, not `src/` — add src root for imports.
_src_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _src_root not in sys.path:
    sys.path.insert(0, _src_root)

# Import our modular components
from src.media.youtube.api import YouTubeAPI
from src.media.youtube.constants import CLO3D_CHANNEL_ID, EXCLUDED_LANGS_AND_WORDS, MD_CHANNEL_ID
from src.media.youtube.subtitles import SubtitleManager
from src.media.youtube.transcripts import TranscriptExtractor
from src.media.youtube.videos import VideoManager
from src.search.ai_search import AISearch
from src.tools.azure import Azure
from src.tools.misc import sanitize_directory_file_name

VIDEO_EXTENSIONS = (
    ".3g2",
    ".3gp",
    ".asf",
    ".avi",
    ".divx",
    ".dv",
    ".f4v",
    ".flv",
    ".m2ts",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".mts",
    ".mxf",
    ".ogv",
    ".rm",
    ".rmvb",
    ".ts",
    ".vob",
    ".webm",
    ".wmv",
)


def sort_transcript_file_name(file_name: str) -> tuple[int, int | str, str]:
    page_match = re.fullmatch(r"page_(\d+)\.json", file_name)
    if page_match:
        return (0, int(page_match.group(1)), file_name.lower())

    return (1, file_name.lower(), file_name.lower())


def parse_video_ids(video_ids_text: str | None) -> list[str]:
    if not video_ids_text:
        return []

    return [video_id.strip() for video_id in re.split(r"[\s,]+", video_ids_text) if video_id.strip()]


def video_id_from_name(name: str) -> str | None:
    match = re.search(r"\[([A-Za-z0-9_-]{6,})\](?:\.[^.]+)?$", name)
    return match.group(1) if match else None


def transcript_has_content(transcript: dict) -> bool:
    transcript_text = transcript.get("transcript")
    if isinstance(transcript_text, str) and transcript_text.strip():
        return True

    markdown_content = transcript.get("markdown_content") or transcript.get("summary")
    if isinstance(markdown_content, str) and markdown_content.strip():
        return True

    chunks = transcript.get("chunks")
    if isinstance(chunks, list):
        for chunk in chunks:
            if isinstance(chunk, dict):
                content = chunk.get("content") or chunk.get("transcript") or chunk.get("markdown_content")
                if isinstance(content, str) and content.strip():
                    return True

    return False


def add_transcript_keys(keys: set[str], transcript: dict, fallback_key: str | None = None) -> None:
    if not transcript_has_content(transcript):
        return

    if fallback_key:
        keys.add(fallback_key)

    video_id = transcript.get("video_id")
    if video_id:
        keys.add(str(video_id))

    title = transcript.get("title")
    if isinstance(title, str) and title.strip():
        keys.add(sanitize_directory_file_name(title).replace(" ", "_"))


def get_existing_transcript_keys(transcript_dir: str) -> set[str]:
    keys = set()
    if not os.path.isdir(transcript_dir):
        return keys

    for transcript_file in os.listdir(transcript_dir):
        if not transcript_file.endswith(".json"):
            continue

        transcript_path = os.path.join(transcript_dir, transcript_file)
        file_stem = os.path.splitext(transcript_file)[0]

        try:
            with open(transcript_path, "r", encoding="utf-8") as f:
                transcript = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue

        if isinstance(transcript, dict):
            add_transcript_keys(keys, transcript, file_stem)
        elif isinstance(transcript, list):
            for transcript_item in transcript:
                if isinstance(transcript_item, dict):
                    add_transcript_keys(keys, transcript_item)

    return keys


def has_existing_transcript_for_video(folder_name: str, video_file: str, transcript_keys: set[str]) -> bool:
    video_stem = os.path.splitext(video_file)[0]
    folder_stem = re.sub(r"\s+\[[^\]]+\]$", "", folder_name)
    video_id = video_id_from_name(folder_name) or video_id_from_name(video_file)

    return video_stem in transcript_keys or folder_stem in transcript_keys or bool(video_id and video_id in transcript_keys)


def find_existing_srt_for_video(folder_path: str, video_file: str) -> str | None:
    video_stem = os.path.splitext(video_file)[0]
    expected_srt = os.path.join(folder_path, f"{video_stem}.srt")
    if os.path.exists(expected_srt):
        return expected_srt

    try:
        folder_files = os.listdir(folder_path)
    except OSError:
        return None

    for file_name in folder_files:
        file_stem, file_extension = os.path.splitext(file_name)
        if file_extension.lower() == ".srt" and file_stem == video_stem:
            return os.path.join(folder_path, file_name)

    return None


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


def main():
    app = questionary.select("What do you want to do?", choices=["Chat Bot", "CLO API"]).ask()
    stage = questionary.select("Which stage?", choices=["dev", "prod"]).ask()
    brand = questionary.select("Which brand?", choices=["clo3d", "closet", "connect", "md", "allinone"]).ask()
    task = questionary.select(
        "What task?",
        choices=[
            "Get List of All Videos",
            "Get All Transcripts By Age",
            "Get All Transcripts By Date",
            "Download All Videos",
            "Generate All Subtitles from Downloaded Videos",
            "Generate All Transcripts from Downloaded Videos",
            "Generate All Markdown Content",
            "Chunk All Documents",
            "Upload All Transcripts",
            "Chunk Documents",
            "Get Transcript",
            "Generate Subtitle from a Downloaded Video",
            "Generate Transcript from a Downloaded Video",
            "Generate Markdown Content",
            "Find & Delete AI Search Documents",
        ],
    ).ask()

    azure = Azure(app, brand, stage)

    youtube_channel_dir_path = os.path.join(os.getcwd(), "data", azure.brand, "youtube", "channel")

    # Initialize all components
    youtube_api = YouTubeAPI(azure, youtube_channel_dir_path)
    transcript_extractor = TranscriptExtractor(azure, youtube_channel_dir_path)
    video_manager = VideoManager(azure, youtube_channel_dir_path)
    subtitle_manager = SubtitleManager(azure, youtube_channel_dir_path)
    ai_search = AISearch(azure)

    channel_id = MD_CHANNEL_ID if brand == "md" else CLO3D_CHANNEL_ID

    if task == "Get List of All Videos":
        video_age = questionary.select("Video Age", choices=["Years", "Months", "Weeks", "Days"]).ask()
        video_age_number = questionary.text(f"Number of {video_age}:").ask()

        if video_age == "Years":
            videos = youtube_api.get_videos_by_age(channel_id=channel_id, video_age_in_years=video_age_number)
            file_name = f"videos_within_{video_age_number}_{video_age.lower()}.txt"
        elif video_age == "Months":
            videos = youtube_api.get_videos_by_age(channel_id=channel_id, video_age_in_months=video_age_number)
            file_name = f"videos_within_{video_age_number}_{video_age.lower()}.txt"
        elif video_age == "Weeks":
            videos = youtube_api.get_videos_by_age(channel_id=channel_id, video_age_in_weeks=video_age_number)
            file_name = f"videos_within_{video_age_number}_{video_age.lower()}.txt"
        elif video_age == "Days":
            videos = youtube_api.get_videos_by_age(channel_id=channel_id, video_age_in_days=video_age_number)
            file_name = f"videos_within_{video_age_number}_{video_age.lower()}.txt"

        youtube_links = []
        for page in videos:
            if "items" not in page:
                continue

            for item in page["items"]:
                if "videoId" not in item["id"]:
                    continue

                youtube_links.append(f"https://www.youtube.com/watch?v={item['id']['videoId']}")

        with open(os.path.join(youtube_channel_dir_path, file_name), "w+", encoding="utf-8") as f:
            f.write("\n".join(youtube_links))

    elif task == "Get All Transcripts By Age":
        video_age = questionary.select("Video Age", choices=["Years", "Months", "Weeks", "Days"]).ask()
        video_age_number = questionary.text(f"Number of {video_age}:").ask()

        video_ids_file_name = f"video_ids_within_{video_age_number}_{video_age.lower()}.json"
        if os.path.exists(os.path.join(youtube_channel_dir_path, video_ids_file_name)):
            with open(os.path.join(youtube_channel_dir_path, video_ids_file_name), "r", encoding="utf-8") as f:
                video_ids = json.load(f)
        else:
            if video_age == "Years":
                video_ids = youtube_api.get_videos_by_age(channel_id=channel_id, video_age_in_years=video_age_number)
            elif video_age == "Months":
                video_ids = youtube_api.get_videos_by_age(channel_id=channel_id, video_age_in_months=video_age_number)
            elif video_age == "Weeks":
                video_ids = youtube_api.get_videos_by_age(channel_id=channel_id, video_age_in_weeks=video_age_number)
            elif video_age == "Days":
                video_ids = youtube_api.get_videos_by_age(channel_id=channel_id, video_age_in_days=video_age_number)

        transcript_extractor.extract_youtube_transcripts(video_ids)

    elif task == "Get All Transcripts By Date":
        start_date = questionary.text("Start Date (YYYY-MM-DD):").ask()
        end_date = questionary.text("End Date (YYYY-MM-DD):", default=datetime.now().strftime("%Y-%m-%d")).ask()

        if os.path.exists(os.path.join(youtube_channel_dir_path, f"{start_date}_{end_date}_videos.json")):
            with open(os.path.join(youtube_channel_dir_path, f"{start_date}_{end_date}_videos.json"), "r", encoding="utf-8") as f:
                videos = json.load(f)
        else:
            videos = youtube_api.get_videos_by_date_range(channel_id=channel_id, start_date=start_date + "T00:00:00Z", end_date=end_date + "T23:59:59Z")

        transcript_extractor.extract_youtube_transcripts(videos)

    elif task == "Download All Videos":
        starting_video_index = questionary.text("Starting video index (0 for most recent video):", default="0").ask()
        num_recent_videos = questionary.text("Number of recent videos to download:", default="10").ask()
        video_manager.download_videos_yt_dlp(int(starting_video_index), int(num_recent_videos))

    elif task == "Generate Subtitle from a Downloaded Video":
        video_dir = os.path.join(youtube_channel_dir_path, "videos")
        transcript_dir = os.path.join(youtube_channel_dir_path, "transcripts")
        transcript_keys = get_existing_transcript_keys(transcript_dir)

        if not os.path.isdir(video_dir):
            print(f"Video directory not found: {video_dir}")
            return

        video_folders = sorted(
            [
                folder
                for folder in os.listdir(video_dir)
                if os.path.isdir(os.path.join(video_dir, folder))
                and not any(lang.lower() in folder.lower() for lang in EXCLUDED_LANGS_AND_WORDS)
                and any(file.endswith(VIDEO_EXTENSIONS) for file in os.listdir(os.path.join(video_dir, folder)))
            ],
            key=str.lower,
        )

        if not video_folders:
            print(f"No downloaded video folders found under: {video_dir}")
            return

        selected_folder = questionary.select("Which downloaded video?", choices=video_folders).ask()
        if not selected_folder:
            return

        folder_path = os.path.join(video_dir, selected_folder)
        video_files = sorted(
            [file for file in os.listdir(folder_path) if file.endswith(VIDEO_EXTENSIONS)],
            key=str.lower,
        )

        if len(video_files) > 1:
            video_file = questionary.select("Which video file?", choices=video_files).ask()
            if not video_file:
                return
        else:
            video_file = video_files[0]

        srt_file = os.path.join(folder_path, f"{os.path.splitext(video_file)[0]}.srt")
        existing_srt = find_existing_srt_for_video(folder_path, video_file)
        if existing_srt:
            print(f"Subtitle already exists, skipping: {video_file}")
            return

        if has_existing_transcript_for_video(selected_folder, video_file, transcript_keys):
            print(f"Transcript exists, skipping subtitle generation: {video_file}")
            return

        subtitle_manager.generate_subtitle(os.path.join(folder_path, video_file), srt_file)

    elif task == "Generate All Subtitles from Downloaded Videos":
        video_dir = os.path.join(youtube_channel_dir_path, "videos")
        transcript_dir = os.path.join(youtube_channel_dir_path, "transcripts")
        transcript_keys = get_existing_transcript_keys(transcript_dir)

        if not os.path.isdir(video_dir):
            print(f"Video directory not found: {video_dir}")
            return

        processed = 0
        skipped_existing_srt = 0
        skipped_existing_transcript = 0
        skipped_filtered_folder = 0
        skipped_non_video = 0

        for folder in os.listdir(video_dir):
            folder_path = os.path.join(video_dir, folder)
            if os.path.isdir(folder_path):
                if any(lang.lower() in folder.lower() for lang in EXCLUDED_LANGS_AND_WORDS):
                    skipped_filtered_folder += 1
                    continue

                for video_file in os.listdir(folder_path):
                    if not video_file.endswith(VIDEO_EXTENSIONS):
                        skipped_non_video += 1
                        continue

                    existing_srt = find_existing_srt_for_video(folder_path, video_file)
                    if existing_srt:
                        skipped_existing_srt += 1
                        print(f"Subtitle already exists, skipping: {video_file}")
                        continue

                    if has_existing_transcript_for_video(folder, video_file, transcript_keys):
                        skipped_existing_transcript += 1
                        print(f"Transcript exists, skipping: {video_file}")
                        continue

                    video_path = os.path.join(folder_path, video_file)
                    output_srt_path = os.path.join(folder_path, f"{os.path.splitext(video_file)[0]}.srt")

                    processed += 1
                    subtitle_manager.generate_subtitle(video_path, output_srt_path)

        print(
            "Generate All Subtitles complete: "
            f"processed {processed}, "
            f"skipped existing SRT {skipped_existing_srt}, "
            f"skipped existing transcript {skipped_existing_transcript}, "
            f"skipped filtered folders {skipped_filtered_folder}, "
            f"skipped non-video files {skipped_non_video}."
        )

    elif task == "Generate Transcript from a Downloaded Video":
        video_dir = os.path.join(youtube_channel_dir_path, "videos")

        if not os.path.isdir(video_dir):
            print(f"Video directory not found: {video_dir}")
            return

        video_folders = sorted(
            [
                folder
                for folder in os.listdir(video_dir)
                if os.path.isdir(os.path.join(video_dir, folder)) and any(file.endswith(VIDEO_EXTENSIONS) for file in os.listdir(os.path.join(video_dir, folder)))
            ],
            key=str.lower,
        )

        if not video_folders:
            print(f"No downloaded video folders found under: {video_dir}")
            return

        selected_folder = questionary.select("Which downloaded video?", choices=video_folders).ask()
        if not selected_folder:
            return

        video_manager.prepare_transcripts_from_dl_videos(os.path.join(video_dir, selected_folder))

    elif task == "Generate All Transcripts from Downloaded Videos":
        video_dir = os.path.join(youtube_channel_dir_path, "videos")

        for folder in os.listdir(video_dir):
            folder_path = os.path.join(video_dir, folder)
            if os.path.isdir(folder_path):
                video_manager.prepare_transcripts_from_dl_videos(folder_path)

    elif task == "Generate All Markdown Content":
        transcript_dir = os.path.join(youtube_channel_dir_path, "transcripts")
        files = [os.path.join(transcript_dir, f) for f in os.listdir(transcript_dir) if f.endswith(".json")]

        with ThreadPoolExecutor(max_workers=10) as executor:
            # We map the instance method to the list of file paths.
            list(tqdm(executor.map(transcript_extractor.generate_markdown_content, files), total=len(files), desc="Generating Markdown Content", position=0))

    elif task == "Upload All Transcripts":
        start_date, end_date = prompt_for_date_range()

        if brand == "allinone":
            for folder in os.listdir(os.path.join(os.getcwd(), "data")):
                if folder == "clo3d" or folder == "md":
                    for file in os.listdir(os.path.join(os.getcwd(), "data", folder, "youtube", "channel")):
                        shutil.copy(
                            os.path.join(os.getcwd(), "data", folder, "youtube", "channel", file),
                            os.path.join(youtube_channel_dir_path, f"{folder}_{file}"),
                        )

        transcript_dir = os.path.join(youtube_channel_dir_path, "transcripts")

        files = []
        for file_name in os.listdir(transcript_dir):
            if file_name.endswith(".json"):
                file_path = os.path.join(transcript_dir, file_name)

                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                transcripts = data if isinstance(data, list) else [data]
                if any(transcript.get("published_at") and start_date <= datetime.strptime(transcript["published_at"].split("T")[0], "%Y-%m-%d").date() <= end_date for transcript in transcripts):
                    files.append(file_path)

        def upload_transcripts(file_path: str):
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            transcripts = data if isinstance(data, list) else [data]
            for transcript in transcripts:
                raw_publish_date = transcript.get("published_at")
                if not raw_publish_date:
                    continue

                publish_date = datetime.strptime(raw_publish_date.split("T")[0], "%Y-%m-%d").date()
                if start_date <= publish_date <= end_date:
                    transcript_extractor.upload_transcript(transcript)

        with ThreadPoolExecutor(max_workers=10) as executor:
            list(tqdm(executor.map(upload_transcripts, files), total=len(files), desc="Uploading Transcript Files", position=0))

    elif task in ["Chunk Documents", "Chunk All Documents"]:
        max_words = int(questionary.text("Max words per chunk:", default="500").ask())
        overlap_words = int(questionary.text("Overlap words:", default="75").ask())

        transcript_dir = os.path.join(youtube_channel_dir_path, "transcripts")
        transcript_file_names = sorted(
            [file_name for file_name in os.listdir(transcript_dir) if file_name.endswith(".json")],
            key=sort_transcript_file_name,
        )

        if task == "Chunk Documents":
            selected_file_names = questionary.checkbox("Which transcript files?", choices=transcript_file_names).ask()
        else:
            selected_file_names = transcript_file_names

        files = [os.path.join(transcript_dir, file_name) for file_name in selected_file_names]

        def chunk_transcript_file(file_path: str):
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            chunked_count = 0
            transcripts = data if isinstance(data, list) else [data]
            for transcript in transcripts:
                chunks = transcript_extractor.build_chunked_documents(
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

        print(f"Chunks saved in {len(files)} transcript file(s) under: {transcript_dir}")

    elif task == "Get Transcript":
        video_ids = parse_video_ids(questionary.text("Video IDs:").ask())

        for video_id in video_ids:
            video_details = youtube_api.video_details(video_id)

            subtitle_manager.download_srt_yt_dlp(video_id)

            file = f"{video_details['title']}.en.srt"
            srt_text = youtube_api.extract_srt_text(os.path.join(youtube_channel_dir_path, "subtitles", file))

            file_name = sanitize_directory_file_name(file.split(".")[0])
            with open(os.path.join(youtube_channel_dir_path, f"{file_name}.json"), "w+", encoding="utf-8") as f:
                json.dump(
                    {
                        "video_id": video_details["video_id"],
                        "url": video_details["url"],
                        "title": video_details["title"],
                        "description": video_details["description"],
                        "transcript": srt_text,
                        "published_at": video_details["published_at"],
                    },
                    f,
                    ensure_ascii=False,
                    indent=4,
                )

    elif task == "Generate Markdown Content":
        transcript_dir = os.path.join(youtube_channel_dir_path, "transcripts")
        youtube_channel_pages = sorted(
            [file_name for file_name in os.listdir(transcript_dir) if file_name.endswith(".json")],
            key=sort_transcript_file_name,
        )
        pages = questionary.checkbox("Which pages?", choices=youtube_channel_pages).ask()
        for page in pages:
            transcript_extractor.generate_markdown_content(os.path.join(transcript_dir, page))

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


if __name__ == "__main__":
    main()
