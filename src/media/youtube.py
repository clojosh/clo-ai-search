import json
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, TypedDict, Union

import questionary
import requests  # type: ignore
import shortuuid
import torch
import yt_dlp
from dateutil.relativedelta import relativedelta
from faster_whisper import WhisperModel
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from tqdm import tqdm
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.formatters import TextFormatter
from youtube_transcript_api.proxies import WebshareProxyConfig

# Running as `python media/youtube.py` puts `media/` on sys.path, not `src/` — add src root for imports.
_src_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _src_root not in sys.path:
    sys.path.insert(0, _src_root)

from search.ai_search import AISearch
from tools.azure import Azure
from tools.misc import check_create_directory, sanitize_directory_file_name

API_KEY = "AIzaSyBFB39J11lWnVU8hqzIn1X1kVeZnxvF8R4"
CLO3D_CHANNEL_ID = "UCApF8J_2QeJ8QPXIAZ25uhw"
MD_CHANNEL_ID = "UCcD-Fd_9s3kmK_fY6qp8u_Q"

YoutubeAPIType = TypedDict(
    "YoutubeAPIType",
    {
        "kind": str,
        "etag": str,
        "nextPageToken": Union[str, None],
        "items": List[dict],
    },
)

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
model = WhisperModel(
    "large-v3",
    device=DEVICE,
    compute_type="float16",  # or "int8_float16" if VRAM constrained
)

EXCLUDED_LANGS_AND_WORDS = {
    "Arabic",
    "Bangla",
    "BahasaBulgarian",
    "BengaluruCantonese",
    "Chinese",
    "Croatian",
    "Czech",
    "Danish",
    "Dutch",
    "Estonian",
    "Finnish",
    "Francais",
    "French",
    "German",
    "Greek",
    "Hindi",
    "Ho_Chi_Minh",
    "Hong_Kong",
    "Hungarian",
    "Indonesia",
    "Italian",
    "Japanese",
    "Korean",
    "Latvian",
    "Lithuanian",
    "Maris",
    "Munich",
    "Norwegian",
    "Paris",
    "Polish",
    "Portuguese",
    "Romanian",
    "Russian",
    "Saigon",
    "Seoul",
    "Serbian",
    "Shanghai",
    "Slovak",
    "Slovenian",
    "Spanish",
    "Swedish",
    "Thai",
    "Ti_ng_Vi_t",
    "Turkce",
    "Turkish",
    "Vietnamese",
}


class YouTube:
    def __init__(self, azure: Azure):
        self.azure = azure

        if not os.path.exists(os.path.join(os.getcwd(), "data", azure.brand, "youtube")):
            os.makedirs(os.path.join(os.getcwd(), "data", azure.brand, "youtube"))

        self.youtube_dir_path = os.path.join(os.getcwd(), "data", azure.brand, "youtube")

        if not os.path.exists(os.path.join(os.getcwd(), "data", azure.brand, "youtube", "channel")):
            os.makedirs(os.path.join(os.getcwd(), "data", azure.brand, "youtube", "channel"))

        self.youtube_channel_dir_path = os.path.join(os.getcwd(), "data", azure.brand, "youtube", "channel")

        if not os.path.exists(os.path.join(os.getcwd(), "data", azure.brand, "youtube", "channel", "transcripts")):
            os.makedirs(os.path.join(os.getcwd(), "data", azure.brand, "youtube", "channel", "transcripts"))

        if not os.path.exists(os.path.join(os.getcwd(), "data", azure.brand, "youtube", "playlist")):
            os.makedirs(os.path.join(os.getcwd(), "data", azure.brand, "youtube", "playlist"))

        self.youtube_playlist_dir_path = os.path.join(os.getcwd(), "data", azure.brand, "youtube", "playlist")

    def video_details(self, video_id: str):
        """
        Retrieves the details of a YouTube video.

        Parameters:
        video_id (str): The ID of the YouTube video.

        Returns:
        dict: A dictionary containing the details of the YouTube video.
        """
        url = f"https://youtube.googleapis.com/youtube/v3/videos?id={video_id}&key=AIzaSyC2LupTSVApfy90Bfzq8L5AAkAawOmT0gY&part=contentDetails%2Cid%2C%20liveStreamingDetails%2C%20localizations%2C%20player%2C%20recordingDetails%2C%20snippet%2C%20statistics%2C%20status%2C%20topicDetails"

        response = requests.get(url).json()

        return {
            "video_id": video_id,
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "title": response["items"][0]["snippet"]["title"],
            "description": response["items"][0]["snippet"]["description"],
            "published_at": response["items"][0]["snippet"]["publishedAt"],
        }

    def extract_srt_text(self, srt_file_path: str):
        """
        This function takes an SRT file path as input and returns the raw text lines
        extracted from the file. It does this by reading the file line by line, skipping
        blank lines, timestamp lines, and lines containing only sequence numbers.

        Args:
            srt_file_path (str): The path to the SRT file to parse.

        Returns:
            str: The raw text lines extracted from the SRT file, joined by spaces.
        """
        raw_text = []
        # Regex to identify timestamp lines (e.g., 00:00:01,000 --> 00:00:04,000)
        timestamp_pattern = re.compile(r"\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}")

        try:
            with open(srt_file_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()

                    # Skip blank lines
                    if not line:
                        # We don't want to include blank lines in our output
                        continue

                    # Skip sequence numbers (lines containing only digits)
                    if line.isdigit():
                        # We don't want to include sequence numbers in our output
                        continue

                    # Skip timestamp lines
                    if timestamp_pattern.match(line):
                        # We don't want to include timestamp lines in our output
                        continue

                    # If the line contains actual text, append it.
                    raw_text.append(line)

            return " ".join(raw_text)

        except FileNotFoundError:
            print(f"\nError: Subtitle file not found at {srt_file_path}", file=sys.stderr)
            # If the file doesn't exist, return None
            return None
        except Exception as e:
            print(f"\nError reading or parsing file {srt_file_path}: {e}", file=sys.stderr)
            # If there's an error reading or parsing the file, return None
            return None

    def get_videos_by_age(self, channel_id: str, video_age_in_years: int = 0, video_age_in_months: int = 0, video_age_in_weeks: int = 0, video_age_in_days: int = 0):
        """
        Retrieves a list of youtube video ids based on the video age parameters.

        Parameters:
        channel_id (str): The ID of the YouTube channel.
        video_age_in_years (int): The age of the videos to retrieve in years. Defaults to 0.
        video_age_in_months (int): The age of the videos to retrieve in months. Defaults to 0.
        video_age_in_weeks (int): The age of the videos to retrieve in weeks. Defaults to 0.
        video_age_in_days (int): The age of the videos to retrieve in days. Defaults to 0.

        Returns:
        list: A list of youtube video ids.
        """

        # Determine the published after date based on the video age parameters
        if int(video_age_in_years) > 0:
            # If the video age is in years, set the published after date to the first day of the year
            published_after = "{}-01-01T00:00:00Z".format(datetime.today().year - int(video_age_in_years))
            file_name = f"video_ids_within_{video_age_in_years}_years.json"
        elif int(video_age_in_months) > 0:
            # If the video age is in months, set the published after date to the first day of the month
            datetime_months = datetime.now() - relativedelta(months=int(video_age_in_months))
            published_after = datetime_months.strftime("%Y-%m-%dT%H:%M:%SZ")
            file_name = f"video_ids_within_{video_age_in_months}_months.json"
        elif int(video_age_in_weeks) > 0:
            # If the video age is in weeks, set the published after date to the first day of the week
            datetime_weeks = datetime.now() - relativedelta(weeks=int(video_age_in_weeks))
            published_after = datetime_weeks.strftime("%Y-%m-%dT%H:%M:%SZ")
            file_name = f"video_ids_within_{video_age_in_weeks}_weeks.json"
        elif int(video_age_in_days) > 0:
            # If the video age is in days, set the published after date to the first day of the day
            datetime_days = datetime.now() - relativedelta(days=int(video_age_in_days))
            published_after = datetime_days.strftime("%Y-%m-%dT%H:%M:%SZ")
            file_name = f"video_ids_within_{video_age_in_days}_days.json"
        else:
            # If no video age parameters are provided, set the published after date to yesterday
            datetime_days = datetime.now() - relativedelta(days=1)
            published_after = datetime_days.strftime("%Y-%m-%dT%H:%M:%SZ")
            file_name = "video_ids_within_1_days.json"

        try:
            # Initialize the YouTube API client
            youtube = build("youtube", "v3", developerKey=API_KEY)

            videos = []
            next_page_token = None

            while True:
                search_response = (
                    youtube.search()
                    .list(
                        part="snippet",
                        channelId=channel_id,
                        type="video",
                        order="date",  # Sort by date for chronological order
                        publishedAfter=published_after,
                        maxResults=30,  # Max allowed results per request is 50
                        pageToken=next_page_token,
                    )
                    .execute()
                )

                for search_item in search_response.get("items", []):
                    video_id = search_item["id"]["videoId"]
                    video_title = search_item["snippet"]["title"]
                    description = search_item["snippet"]["description"]
                    published_at = search_item["snippet"]["publishedAt"]

                    videos.append({"id": video_id, "title": video_title, "description": description, "published_at": published_at})

                # Check for the next page of results
                next_page_token = search_response.get("nextPageToken")

                if not next_page_token:
                    break

            print(f"\n--- Found Estimated {search_response.get('pageInfo', {}).get('totalResults', 0)} total videos ---")
            print(f"Saved {len(videos)} videos")

            with open(os.path.join(self.youtube_channel_dir_path, f"{file_name}"), "w", encoding="utf-8") as f:
                json.dump(videos, f, ensure_ascii=False, indent=4)

            return videos

        except HttpError as e:
            print(f"An HTTP error {e.resp.status} occurred: {e.content}")
            return []
        except Exception as e:
            print(f"An unexpected error occurred: {e}")
            return []

    def get_videos_by_date_range(self, channel_id: str, start_date: str, end_date: str):
        """
        Retrieves all video details for a given channel published within a date range.

        :param channel_id: The ID of the channel.
        :param start_date: RFC 3339 formatted date (publishedAfter).
        :param end_date: RFC 3339 formatted date (publishedBefore).

        :return: A list of dictionaries, each containing video snippet data.
        """
        try:
            # Initialize the YouTube API client
            youtube = build("youtube", "v3", developerKey=API_KEY)

            videos = []
            next_page_token = None

            while True:
                # The search().list method is used for filtering by date and channel.
                search_response = (
                    youtube.search()
                    .list(
                        part="snippet",
                        channelId=channel_id,
                        type="video",
                        order="date",  # Sort by date for chronological order
                        publishedAfter=start_date,
                        publishedBefore=end_date,
                        maxResults=30,  # Max allowed results per request is 50
                        pageToken=next_page_token,
                    )
                    .execute()
                )

                for search_item in search_response.get("items", []):
                    video_id = search_item["id"]["videoId"]
                    video_title = search_item["snippet"]["title"]
                    description = search_item["snippet"]["description"]
                    published_at = search_item["snippet"]["publishedAt"]

                    videos.append({"id": video_id, "title": video_title, "description": description, "published_at": published_at})

                # Check for the next page of results
                next_page_token = search_response.get("nextPageToken")

                if not next_page_token:
                    break

            print(f"\n--- Found Estimated {search_response.get('pageInfo', {}).get('totalResults', 0)} total videos ---")
            print(f"Saved {len(videos)} videos")

            start_date_formatted = datetime.strptime(start_date, "%Y-%m-%dT%H:%M:%SZ").strftime("%Y-%m-%d")
            end_date_formatted = datetime.strptime(end_date, "%Y-%m-%dT%H:%M:%SZ").strftime("%Y-%m-%d")
            print(f"Saved {len(videos)} videos to {os.path.join(self.youtube_channel_dir_path, f'{start_date_formatted}_{end_date_formatted}_videos.json')}")

            with open(os.path.join(self.youtube_channel_dir_path, f"{start_date_formatted}_{end_date_formatted}_videos.json"), "w", encoding="utf-8") as f:
                json.dump(videos, f, ensure_ascii=False, indent=4)

            return videos

        except HttpError as e:
            print(f"An HTTP error {e.resp.status} occurred: {e.content}")
            return []
        except Exception as e:
            print(f"An unexpected error occurred: {e}")
            return []

    # Works on MacOS
    def download_srt_yt_dlp(self, video_id: str):
        ydl_opts = {
            "writeautomaticsub": True,
            "subtitleslangs": ["en"],
            "subtitlesformat": "srt",
            "skip_download": True,
            "outtmpl": f"{self.youtube_channel_dir_path}/subtitles/%(title)s.%(ext)s",
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([f"https://www.youtube.com/watch?v={video_id}"])

    def download_srt_yt_dlp_subprocess(self, video_id: str):
        """
        Executes the yt-dlp command using subprocess.run() to download subtitles.
        """

        YT_DLP_COMMAND = [
            "yt-dlp",
            "--write-subs",  # Write a subtitle file
            "--write-auto-subs",  # Write automatically generated subtitles (if available)
            "--sub-langs",
            "en",  # Specify the subtitle language to be English
            "--skip-download",  # Skip downloading the video file
            "--sub-format",
            "srt",  # Convert the subtitle format to SRT
            "-o",
            f"{self.youtube_channel_dir_path}/subtitles/%(title)s.%(ext)s",
            f"https://www.youtube.com/watch?v={video_id}",  # The target YouTube URL
        ]

        print("\n--- Running yt-dlp Command ---")
        # Print the command being run for transparency
        print(" ".join(YT_DLP_COMMAND))
        print("-" * 60)

        try:
            # subprocess.run is the recommended way to run external commands.
            # check=True: raises CalledProcessError if the command returns a non-zero exit code.
            # capture_output=True: captures stdout and stderr.
            # text=True: decodes stdout/stderr as text.
            result = subprocess.run(
                YT_DLP_COMMAND,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",  # Ensure proper decoding of output
            )

            print("\n--- Command Executed Successfully ---")
            print("\nSTDOUT (Output from yt-dlp):")
            # Print the standard output (this usually shows download progress/completion)
            print(result.stdout)

        except subprocess.CalledProcessError as e:
            # Handle errors reported by yt-dlp itself (e.g., video unavailable, invalid URL)
            print("\n--- ERROR: Command Failed ---", file=sys.stderr)
            print(f"Exit code: {e.returncode}", file=sys.stderr)
            print("STDERR (Error output):", file=sys.stderr)
            # Print the error message from the program
            print(e.stderr, file=sys.stderr)

        except FileNotFoundError:
            # Handle the case where the 'yt-dlp' executable is not found in the system PATH
            print("\n--- FATAL ERROR: Executable Not Found ---", file=sys.stderr)
            print("Error: 'yt-dlp' command not found.", file=sys.stderr)
            print("Please ensure yt-dlp is installed and accessible in your system's PATH.", file=sys.stderr)
            # Suggest installing
            print("You can usually install it using: pip install yt-dlp", file=sys.stderr)

        except Exception as e:
            # Catch any other unexpected exceptions
            print(f"\n--- An unexpected error occurred: {e} ---", file=sys.stderr)

    def download_transcripts_yt_transcript_api(self, video_id: str):
        try:
            ytt_api = YouTubeTranscriptApi(
                proxy_config=WebshareProxyConfig(
                    proxy_username="fujzysxp-us-9",
                    proxy_password="mnefpap1bq31",
                )
            )

            # 1. Get the list of available transcripts
            transcript_list = ytt_api.list(video_id)

            try:
                # 2. Get the manually created transcript if available
                retrieved__transcript = transcript_list.find_manually_created_transcript(["en"])

            except Exception:
                print("\nUnable to retrieve manually created transcript. Attempting to retrieve generated transcript...")
                retrieved__transcript = transcript_list.find_generated_transcript(["en"])
                print("Generated Transcript successfully retrieved.")

            # 3. Use a formatter to convert the raw data into a clean text string
            formatter = TextFormatter()
            formatted_transcript = formatter.format_transcript(retrieved__transcript.fetch())

            return formatted_transcript

        except Exception as e:
            exception = str(e)
            error_message = exception[: exception.index("If you are")].strip()
            print(f"\nError occurred: {error_message}")

        return ""

    # Youtube blocks some requests from this
    def download_videos_yt_dlp(self):
        """Downloads only standard published videos (no Shorts, no Live)."""

        # Using /videos ensures we start on the main uploads tab
        CHANNEL_URL = "https://www.youtube.com/@CLO3D/videos" if self.azure.brand == "clo3d" else "https://www.youtube.com/@MarvelousDesigner/videos"
        OUTPUT_DIR = os.path.join(self.youtube_channel_dir_path, "videos")
        NUM_RECENT_VIDEOS = 100

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
            "format": "bestvideo[height<=480]+bestaudio/best[height<=480]",
            "http_headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"},
            "playlist_items": f"4-{NUM_RECENT_VIDEOS}",
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

        print(f"Searching for {NUM_RECENT_VIDEOS} videos...")

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([CHANNEL_URL])
            print("\n✅ Download process complete!")
        except Exception as e:
            print(f"\n❌ Error: {e}")

    def extract_youtube_transcripts(self, videos: list) -> None:
        """
        Extracts transcripts from a youtube channel

        Args:
            videos (list): The response from the youtube api
        """

        transcripts = []

        for i, video in enumerate(videos):
            print("\nRetrieving transcript for:\n" + video["title"])

            try:
                # Check if subtitle exists, if not download it
                # if not os.path.exists(os.path.join(self.youtube_channel_dir_path, "subtitles", video["title"] + ".en.srt")):
                #     self.download_srt(video["videoId"])
                # else:
                #     print("Subtitle already exists")

                # if not os.path.exists(os.path.join(self.youtube_channel_dir_path, "subtitles", video["title"] + ".en.srt")):
                #     # print("No subtitles found for:\n" + video["title"] + "\n")
                #     continue

                # transcript = self.extract_srt_text(
                #     os.path.join(self.youtube_channel_dir_path, "subtitles", video["title"] + ".en.srt")
                # ).replace("  ", " ")

                transcript = self.download_transcripts_yt_transcript_api(video["id"])

            except Exception:
                continue

            # Create a dictionary to store the video data
            video = {
                "video_id": video["id"] if video["id"] else shortuuid.uuid(),  # Get the video id
                "url": f"https://www.youtube.com/watch?v={video['id']}",  # Get the video url
                "title": video["title"].title().replace("&#39;", "'").replace("&quot;", '"').replace("&amp;", "&"),
                "description": video["description"],  # YouTube has its own description
                "transcript": transcript,
                "published_at": video["published_at"],  # Get the video publish date
            }

            transcripts.append(video)

            if (i + 1) % 30 == 0 or (i + 1) == len(videos):
                # Save the transcripts to a json file every 30 videos to avoid losing data if the process is interrupted
                page_number = (i + 1) // 30 if (i + 1) % 30 == 0 else ((i + 1) // 30) + 1
                with open(f"{os.path.join(self.youtube_channel_dir_path, 'transcripts', f'page_{page_number}')}.json", "w+", encoding="utf-8") as f:
                    json.dump(transcripts, f, ensure_ascii=False, indent=4)

                transcripts = []

    def extract_youtube_playlist_transcripts(self, playlist_url: str):
        """Extract the transcripts of every video in a playlist and save them into a JSON file"""
        playlist_title, videos = self.extract_youtube_playlist_videos(playlist_url)
        playlist_title = sanitize_directory_file_name(playlist_title)

        transcripts = []
        for video in tqdm(videos, desc="Extractng YouTube Transcripts", colour="blue", leave=False):
            transcript = {
                "title": video["title"],
                "source": video["url"],
                "transcript": YouTube.extract_video_transcript_text(video["url"]),
                "youtube_links": [video["url"]],
            }
            transcripts.append(transcript)

        for transcript in transcripts:
            buffer = []
            playlist_dir_path = os.path.join(self.youtube_playlist_dir_path, playlist_title)
            check_create_directory(playlist_dir_path)

            buffer.append(transcript)
            with open(f"{os.path.join(playlist_dir_path, sanitize_directory_file_name(transcript['title']))}.json", "w+", encoding="utf-8") as f:
                json.dump(buffer, f, ensure_ascii=False, indent=4)

        return playlist_title

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

        file_path = Path(video_path)
        print(f"\nGenerating subtitles for {file_path.stem}")

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
                with open(os.path.join(video_dir_path, file), "r", encoding="utf-8") as f:
                    description_data = json.load(f)

                description = description_data.get("description", "")
                transcript = self.extract_srt_text(os.path.join(video_dir_path, file.replace(".info.json", ".srt")))

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

                with open(os.path.join(self.youtube_channel_dir_path, "transcripts", file.replace(".info.json", ".json")), "w+", encoding="utf-8") as f:
                    json.dump(doc, f, ensure_ascii=False, indent=4)

    def summarize_transcripts(self, file_path: str):
        """
        Summarize all the transcripts in a file

        Args:
            file_path (str): The path to the file containing the transcripts to summarize.

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
                    transcripts["summary"] = ""
                else:
                    summary = self.azure.openai_helper.generate_structured_transcript(transcripts["title"], transcripts["transcript"])
                    transcripts["summary"] = summary
            except Exception as e:
                print(f"\nError summarizing transcript for {file_path}:")
                raise e
        else:
            # Summarize each transcript
            for trans in transcripts:
                if transcripts["transcript"] is None:
                    return

                try:
                    if trans["transcript"] == "" or len(trans["transcript"]) < 150:
                        trans["summary"] = ""
                    else:
                        summary = self.azure.openai_helper.generate_structured_transcript(trans["title"], trans["transcript"])
                        trans["summary"] = summary
                except Exception as e:
                    raise e

        with open(file_path, "w", encoding="utf-8") as file:
            json.dump(transcripts, file, ensure_ascii=False, indent=4)

    def upload_transcript(self, transcript: dict):
        """
        Uploads a single transcript to Azure Search

        Args:
            transcript (dict): The transcript to upload

        Returns:
            None
        """
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
                "source": "YouTube",
                "created_at": transcript["published_at"],
                "title_vector": self.azure.openai_helper.generate_embeddings(text=transcript["title"]),
                "content_vector": self.azure.openai_helper.generate_embeddings(text=transcript["summary"]),
            }
        )


if __name__ == "__main__":
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
    yt = YouTube(azure)
    ai_search = AISearch(azure)

    channel_id = MD_CHANNEL_ID if brand == "md" else CLO3D_CHANNEL_ID

    if task == "Get List of All Videos":
        video_age = questionary.select("Video Age", choices=["Years", "Months", "Weeks", "Days"]).ask()
        video_age_number = questionary.text(f"Number of {video_age}:").ask()

        if video_age == "Years":
            video_ids = yt.get_videos_by_age(channel_id=channel_id, video_age_in_years=video_age_number)
        elif video_age == "Months":
            video_ids = yt.get_videos_by_age(channel_id=channel_id, video_age_in_months=video_age_number)
        elif video_age == "Weeks":
            video_ids = yt.get_videos_by_age(channel_id=channel_id, video_age_in_weeks=video_age_number)
        elif video_age == "Days":
            video_ids = yt.get_videos_by_age(channel_id=channel_id, video_age_in_days=video_age_number)

        youtube_links = []
        for page in video_ids:
            if "items" not in page:
                continue

            for item in page["items"]:
                if "videoId" not in item["id"]:
                    continue

                youtube_links.append(f"https://www.youtube.com/watch?v={item['id']['videoId']}")

        with open(os.path.join(yt.youtube_channel_dir_path, "youtube_links.txt"), "w+", encoding="utf-8") as f:
            f.write("\n".join(youtube_links))

    elif task == "Get All Transcripts By Age":
        video_age = questionary.select("Video Age", choices=["Years", "Months", "Weeks", "Days"]).ask()
        video_age_number = questionary.text(f"Number of {video_age}:").ask()

        video_ids_file_name = f"video_ids_within_{video_age_number}_{video_age.lower()}.json"
        if os.path.exists(os.path.join(yt.youtube_channel_dir_path, video_ids_file_name)):
            with open(os.path.join(yt.youtube_channel_dir_path, video_ids_file_name), "r", encoding="utf-8") as f:
                video_ids = json.load(f)
        else:
            if video_age == "Years":
                video_ids = yt.get_videos_by_age(channel_id=channel_id, video_age_in_years=video_age_number)
            elif video_age == "Months":
                video_ids = yt.get_videos_by_age(channel_id=channel_id, video_age_in_months=video_age_number)
            elif video_age == "Weeks":
                video_ids = yt.get_videos_by_age(channel_id=channel_id, video_age_in_weeks=video_age_number)
            elif video_age == "Days":
                video_ids = yt.get_videos_by_age(channel_id=channel_id, video_age_in_days=video_age_number)

        yt.extract_youtube_transcripts(video_ids)

    elif task == "Get All Transcripts By Date":
        start_date = questionary.text("Start Date (YYYY-MM-DD):").ask()
        end_date = questionary.text("End Date (YYYY-MM-DD):", default=datetime.now().strftime("%Y-%m-%d")).ask()

        if os.path.exists(os.path.join(yt.youtube_channel_dir_path, f"{start_date}_{end_date}_videos.json")):
            with open(os.path.join(yt.youtube_channel_dir_path, f"{start_date}_{end_date}_videos.json"), "r", encoding="utf-8") as f:
                videos = json.load(f)
        else:
            videos = yt.get_videos_by_date_range(channel_id=channel_id, start_date=start_date + "T00:00:00Z", end_date=end_date + "T23:59:59Z")

        yt.extract_youtube_transcripts(videos)

    elif task == "Download All Videos":
        yt.download_videos_yt_dlp()

    elif task == "Generate All Subtitles":
        video_dir = os.path.join(yt.youtube_channel_dir_path, "videos")

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

                        yt.generate_subtitle(video_path, output_srt_path)

    elif task == "Prepare Transcripts from Downloaded Videos":
        video_dir = os.path.join(yt.youtube_channel_dir_path, "videos")

        for folder in os.listdir(video_dir):
            folder_path = os.path.join(video_dir, folder)
            if os.path.isdir(folder_path):
                yt.prepare_transcripts_from_dl_videos(folder_path)

    elif task == "Summarize All Transcripts":
        transcript_dir = os.path.join(yt.youtube_channel_dir_path, "transcripts")
        files = [os.path.join(transcript_dir, f) for f in os.listdir(transcript_dir) if f.endswith(".json")]

        with ThreadPoolExecutor(max_workers=5) as executor:
            # We map the instance method 'yt.summarize_transcripts' directly to the list of file paths.
            # Python automatically passes 'yt' as 'self'.
            list(tqdm(executor.map(yt.summarize_transcripts, files), total=len(files), desc="Summarizing Transcripts", position=0))

    elif task == "Upload All Transcripts":
        if brand == "allinone":
            for folder in os.listdir(os.path.join(os.getcwd(), "data")):
                if folder == "clo3d" or folder == "md":
                    for file in os.listdir(os.path.join(os.getcwd(), "data", folder, "youtube", "channel")):
                        shutil.copy(
                            os.path.join(os.getcwd(), "data", folder, "youtube", "channel", file),
                            os.path.join(yt.youtube_channel_dir_path, f"{folder}_{file}"),
                        )

        transcript_dir = os.path.join(yt.youtube_channel_dir_path, "transcripts")
        files = [os.path.join(transcript_dir, f) for f in os.listdir(transcript_dir) if f.endswith(".json")]

        # 1. Determine the target list (files vs. internal items)
        if len(files) == 1:
            # Single file mode: Process items inside the JSON
            with open(files[0], "r", encoding="utf-8") as f:
                data = json.load(f)

            # Assuming the JSON is a list of items; adjust if it's a dict
            work_items = data if isinstance(data, list) else [data]
            process_func = yt.upload_transcript
            description = "Uploading Items from Single File"
        else:
            # Multi-file mode: Process each file path
            work_items = files

            def upload_transcripts(file_path: str):
                with open(file_path, "r", encoding="utf-8") as f:
                    transcripts = json.load(f)

                for transcript in transcripts:
                    yt.upload_transcript(transcript)

            process_func = upload_transcripts
            description = "Uploading Transcript Files"

        # 2. Execute based on the determined context
        with ThreadPoolExecutor(max_workers=10) as executor:
            list(tqdm(executor.map(process_func, work_items), total=len(work_items), desc=description, position=0))

    elif task == "Get Transcript":
        video_id = questionary.text("Video ID:").ask()

        video_details = yt.video_details(video_id)

        yt.download_srt_yt_dlp(video_id)

        file = f"{video_details['title']}.en.srt"
        srt_text = yt.extract_srt_text(os.path.join(yt.youtube_channel_dir_path, "subtitles", file))

        file_name = sanitize_directory_file_name(file.split(".")[0])
        with open(os.path.join(yt.youtube_channel_dir_path, f"{file_name}.json"), "w+", encoding="utf-8") as f:
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
        youtube_channel_pages = sorted(os.listdir(os.path.join(yt.youtube_channel_dir_path, "transcripts")), key=lambda x: int(x.split("_")[1].split(".")[0]))
        page = questionary.select("Which page?", choices=youtube_channel_pages).ask()
        yt.summarize_transcripts(stage, brand, os.path.join(yt.youtube_channel_dir_path, "transcripts", page), 0)

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
                ai_search.delete_ai_search_document(document["article_id"])
                ai_search.delete_ai_search_document(document["article_id"])
