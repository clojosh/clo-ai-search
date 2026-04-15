#!/usr/bin/env python3
"""
Main YouTube module that orchestrates all YouTube-related functionality.
This file contains the main entry point and orchestration logic for YouTube processing tasks.
"""

import json
import os
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

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


def main():
    stage = questionary.select("Which stage?", choices=["dev", "prod"]).ask()
    brand = questionary.select("Which brand?", choices=["clo3d", "md", "allinone"]).ask()
    task = questionary.select(
        "What task?",
        choices=[
            "Get List of All Videos",
            "Get All Transcripts By Age",
            "Get All Transcripts By Date",
            "Download All Videos",
            "Generate All Subtitles",
            "Prepare Transcripts from Downloaded Videos",
            "Summarize All Transcripts",
            "Upload All Transcripts",
            "Get Transcript",
            "Summarize Transcript",
            "Find & Delete AI Search Documents",
        ],
    ).ask()

    azure = Azure(stage, brand)

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
        video_manager.download_videos_yt_dlp()

    elif task == "Generate All Subtitles":
        video_dir = os.path.join(youtube_channel_dir_path, "videos")

        for folder in os.listdir(video_dir):
            folder_path = os.path.join(video_dir, folder)
            if os.path.isdir(folder_path):
                if any(lang.lower() in folder.lower() for lang in EXCLUDED_LANGS_AND_WORDS):
                    continue

                for video_file in os.listdir(folder_path):
                    # Check if srt file already exists
                    srt_file = os.path.join(folder_path, f"{os.path.splitext(video_file)[0]}.srt")
                    if os.path.exists(srt_file):
                        continue

                    if video_file.endswith((".mp4", ".mkv", ".avi")):
                        video_path = os.path.join(folder_path, video_file)
                        output_srt_path = os.path.join(folder_path, f"{os.path.splitext(video_file)[0]}.srt")

                        subtitle_manager.generate_subtitle(video_path, output_srt_path)

    elif task == "Prepare Transcripts from Downloaded Videos":
        video_dir = os.path.join(youtube_channel_dir_path, "videos")

        for folder in os.listdir(video_dir):
            folder_path = os.path.join(video_dir, folder)
            if os.path.isdir(folder_path):
                video_manager.prepare_transcripts_from_dl_videos(folder_path)

    elif task == "Summarize All Transcripts":
        transcript_dir = os.path.join(youtube_channel_dir_path, "transcripts")
        files = [os.path.join(transcript_dir, f) for f in os.listdir(transcript_dir) if f.endswith(".json")]

        with ThreadPoolExecutor(max_workers=5) as executor:
            # We map the instance method to the list of file paths.
            list(tqdm(executor.map(transcript_extractor.summarize_transcripts, files), total=len(files), desc="Summarizing Transcripts", position=0))

    elif task == "Upload All Transcripts":
        if brand == "allinone":
            for folder in os.listdir(os.path.join(os.getcwd(), "data")):
                if folder == "clo3d" or folder == "md":
                    for file in os.listdir(os.path.join(os.getcwd(), "data", folder, "youtube", "channel")):
                        shutil.copy(
                            os.path.join(os.getcwd(), "data", folder, "youtube", "channel", file),
                            os.path.join(youtube_channel_dir_path, f"{folder}_{file}"),
                        )

        transcript_dir = os.path.join(youtube_channel_dir_path, "transcripts")
        files = [os.path.join(transcript_dir, f) for f in os.listdir(transcript_dir) if f.endswith(".json")]

        # 1. Determine the target list (files vs. internal items)
        if len(files) == 1:
            # Single file mode: Process items inside the JSON
            with open(files[0], "r", encoding="utf-8") as f:
                data = json.load(f)

            # Assuming the JSON is a list of items; adjust if it's a dict
            work_items = data if isinstance(data, list) else [data]
            process_func = transcript_extractor.upload_transcript
            description = "Uploading Items from Single File"
        else:
            # Multi-file mode: Process each file path
            work_items = files

            def upload_transcripts(file_path: str):
                with open(file_path, "r", encoding="utf-8") as f:
                    transcripts = json.load(f)

                for transcript in transcripts:
                    transcript_extractor.upload_transcript(transcript)

            process_func = upload_transcripts
            description = "Uploading Transcript Files"

        # 2. Execute based on the determined context
        with ThreadPoolExecutor(max_workers=10) as executor:
            list(tqdm(executor.map(process_func, work_items), total=len(work_items), desc=description, position=0))

    elif task == "Get Transcript":
        video_id = questionary.text("Video ID:").ask()

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

    elif task == "Summarize Transcript":
        youtube_channel_pages = sorted(os.listdir(os.path.join(youtube_channel_dir_path, "transcripts")), key=lambda x: int(x.split("_")[2].split(".")[0]))
        page = questionary.select("Which page?", choices=youtube_channel_pages).ask()
        transcript_extractor.summarize_transcripts(os.path.join(youtube_channel_dir_path, "transcripts", page))

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


if __name__ == "__main__":
    main()
