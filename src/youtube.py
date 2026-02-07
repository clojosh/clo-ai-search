import json
import multiprocessing
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from typing import List, TypedDict, Union

import questionary
import requests  # type: ignore
import shortuuid
import yt_dlp
from dateutil.relativedelta import relativedelta
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from tqdm import tqdm
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.formatters import TextFormatter
from youtube_transcript_api.proxies import WebshareProxyConfig

from ai_search import AISearch
from tools.azure import Azure
from tools.misc import check_create_directory, logger, sanitize_directory_file_name

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
            "VideoId": video_id,
            "Url": f"https://www.youtube.com/watch?v={video_id}",
            "title": response["items"][0]["snippet"]["title"],
            "Description": response["items"][0]["snippet"]["description"],
            "PublishedAt": response["items"][0]["snippet"]["publishedAt"],
        }

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
                # The search().list method is used for filtering by date and channel.
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

            print(f"\n--- Found {search_response.get('pageInfo', {}).get('totalResults', 0)} total videos ---")

            print(f"Saved {len(videos)} videos to {os.path.join(self.youtube_channel_dir_path, f'{file_name}')}")

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

            print(f"\n--- Found {search_response.get('pageInfo', {}).get('totalResults', 0)} total videos ---")

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
            print(f"Error: Subtitle file not found at {srt_file_path}", file=sys.stderr)
            # If the file doesn't exist, return None
            return None
        except Exception as e:
            print(f"Error reading or parsing file {srt_file_path}: {e}", file=sys.stderr)
            # If there's an error reading or parsing the file, return None
            return None

    def download_transcripts_youtube_transcript_api(self, video_id: str):
        try:
            ytt_api = YouTubeTranscriptApi(
                proxy_config=WebshareProxyConfig(
                    proxy_username="fujzysxp",
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
        """Downloads videos from a YouTube channel using yt-dlp."""

        # --- Configuration ---
        CHANNEL_URL = "https://www.youtube.com/@CLO3D"  # <-- CHANGE THIS to the actual channel URL
        OUTPUT_DIR = os.path.join(self.youtube_channel_dir_path, "videos")
        NUM_RECENT_VIDEOS = 10

        # Create the output directory if it doesn't exist
        if not os.path.exists(OUTPUT_DIR):
            os.makedirs(OUTPUT_DIR)

        # --- yt-dlp Options ---
        ydl_opts = {
            "http_headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"},
            # Limit the download to the top N items in the channel's uploads list (most recent first)
            "playlist_items": f"1-{NUM_RECENT_VIDEOS}",
            # 1. Output File Template: Uses the date and title for the filename.
            # It places the downloaded file inside the 'channel_downloads' folder.
            "outtmpl": os.path.join(OUTPUT_DIR, "%(upload_date)s - %(title)s.%(ext)s"),
            # 2. Download Archive: Skips files whose ID is already in archive.txt.
            "download_archive": os.path.join(OUTPUT_DIR, "archive.txt"),
            # 3. Metadata and Thumbnail Embedding
            "writedescription": True,
            "writeinfojson": True,
            "embed_metadata": True,
            "embed_thumbnail": True,
            # 4. Error Handling: Continue on download error
            "ignoreerrors": True,
            # 5. Verbosity
            "quiet": False,  # Set to True to suppress most output
        }

        print(f"Starting download of the {NUM_RECENT_VIDEOS} most recent videos from: {CHANNEL_URL}")
        print(f"Files will be saved to: {OUTPUT_DIR}")

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # The extract_info function will process the channel URL as a playlist
                # and download all available videos based on the options (ydl_opts).
                info_dict = ydl.extract_info(CHANNEL_URL, download=True)

            print("\n✅ Download process complete!")

        except Exception as e:
            print(f"\n❌ An error occurred during the download process: {e}")

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

                transcript = self.download_transcripts_youtube_transcript_api(video["id"])

            except Exception:
                continue

            # Create a dictionary to store the video data
            video = {
                "VideoId": video["id"] if video["id"] else shortuuid.uuid(),  # Get the video id
                "Url": f"https://www.youtube.com/watch?v={video['id']}",  # Get the video url
                "title": video["title"].title().replace("&#39;", "'").replace("&quot;", '"').replace("&amp;", "&"),
                "Description": video["description"],  # YouTube has its own description
                "Transcript": transcript,
                "PublishedAt": video["published_at"],  # Get the video publish date
            }

            transcripts.append(video)

            # Save the transcripts to a json file every 30 videos to avoid losing data if the process is interrupted
            if (i + 1) % 30 == 0 or (i + 1) == len(videos):
                with open(f"{os.path.join(self.youtube_channel_dir_path, 'transcripts', f'page_{i}')}.json", "w+", encoding="utf-8") as f:
                    json.dump(transcripts, f, ensure_ascii=False, indent=4)

    def extract_youtube_playlist_transcripts(self, playlist_url):
        """Extract the transcripts of every video in a playlist and save them into a JSON file"""
        playlist_title, videos = self.extract_youtube_playlist_videos(playlist_url)
        playlist_title = sanitize_directory_file_name(playlist_title)

        transcripts = []
        for video in tqdm(videos, desc="Extractng YouTube Transcripts", colour="blue", leave=False):
            transcript = {
                "title": video["title"],
                "source": video["url"],
                "Transcript": YouTube.extract_video_transcript_text(video["url"]),
                "youtube_links": [video["url"]],
            }
            transcripts.append(transcript)

        for transcript in transcripts:
            buffer = []
            playlist_dir_path = os.path.join(self.youtube_playlist_dir_path, playlist_title)
            check_create_directory(playlist_dir_path)

            buffer.append(transcript)
            with open(f"{os.path.join(playlist_dir_path, sanitize_directory_file_name(transcript['Title']))}.json", "w+", encoding="utf-8") as f:
                json.dump(buffer, f, ensure_ascii=False, indent=4)

        return playlist_title

    @staticmethod
    def summarize_transcripts(params):
        """
        Summarize all the transcripts in a file

        Args:
            params (tuple): A tuple containing the stage, brand, file path, and worker id.

        Returns:
            None
        """
        env, brand, file_path, worker_id = params

        print("\n" + os.path.split(file_path)[1].strip())

        environment = Azure(env, brand)

        # Read the transcripts from the file
        with open(file_path, "r", encoding="utf-8") as file:
            transcripts = json.load(file)

        # Summarize each transcript
        for trans in tqdm(transcripts, desc=f"File: {os.path.basename(file_path)[:15]}...", position=worker_id + 1, leave=False):
            try:
                if trans["Transcript"] == "" or len(trans["Transcript"]) < 150:
                    trans["Summary"] = ""
                else:
                    summary = environment.openai_helper.generate_transcript_summary(trans["Transcript"])
                    trans["Summary"] = summary
            except Exception as e:
                raise e

        with open(file_path, "w", encoding="utf-8") as file:
            json.dump(transcripts, file, ensure_ascii=False, indent=4)

    def mp_summarize_transcripts(self):
        files = os.listdir(os.path.join(self.youtube_channel_dir_path, "transcripts"))
        num_workers = 4  # Adjust based on your API limits

        # Pass the worker index (i % num_workers) so they don't fight for the same line
        summarize_transcripts_params = [(self.stage, self.brand, os.path.join(self.youtube_channel_dir_path, "transcripts", f), i % num_workers) for i, f in enumerate(files)]

        # Main progress bar (Position 0)
        with multiprocessing.Pool(num_workers) as p:
            for _ in tqdm(p.imap_unordered(YouTube.summarize_transcripts, summarize_transcripts_params), total=len(summarize_transcripts_params), desc="Overall Progress", position=0):
                pass

    def upload_transcripts(self):
        for file in tqdm(os.listdir(os.path.join(yt.youtube_channel_dir_path, "transcripts")), desc="Uploading Transcripts", colour="green", position=0, leave=True):
            with open(os.path.join(self.youtube_channel_dir_path, "transcripts", file), "r", encoding="utf-8") as f:
                transcripts = json.load(f)

            upload_transcripts = []
            for transcript in transcripts:
                if transcript["Transcript"] == "":
                    continue

                if "Summary" in transcript:
                    if transcript["Summary"] == "" or len(transcript["Summary"]) < 150:
                        continue

                if transcript["VideoId"].startswith("_"):
                    transcript["VideoId"] = "YT" + transcript["VideoId"]

                upload_transcripts.append(
                    {
                        "@search.action": "mergeOrUpload",
                        "article_id": transcript["VideoId"],
                        "source": transcript["Url"],
                        "title": transcript["title"],
                        "content": transcript["Summary"] if "Summary" in transcript else transcript["Transcript"],
                        "content_description": transcript["Description"],
                        "created_at": transcript["PublishedAt"],
                        "youtube_links": [transcript["Url"]],
                        "title_vector": self.azure.openai_helper.generate_embeddings(text=transcript["title"]),
                        "content_vector": self.azure.openai_helper.generate_embeddings(text=transcript["Summary"]),
                    }
                )

            self.azure.search_client.upload_documents(upload_transcripts)


if __name__ == "__main__":
    stage = questionary.select("Which stage?", choices=["dev", "prod"]).ask()
    brand = questionary.select("Which brand?", choices=["clo3d", "md", "allinone"]).ask()
    task = questionary.select(
        "What task?",
        choices=[
            "Get All Videos",
            "Get Transcript",
            "Get All Transcripts By Age",
            "Get All Transcripts By Date",
            "Download All Videos",
            "Summarize Transcript",
            "Summarize All Transcripts",
            "Upload All Transcripts",
            "Find & Delete AI Search Documents",
        ],
    ).ask()

    azure = Azure(stage, brand)
    yt = YouTube(azure)
    ai_search = AISearch(azure)

    channel_id = MD_CHANNEL_ID if brand == "md" else CLO3D_CHANNEL_ID

    if task == "Get All Videos":
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
            for item in page["items"]:
                if "videoId" not in item["id"]:
                    continue

                youtube_links.append(f"https://www.youtube.com/watch?v={item['id']['videoId']}")

        with open(os.path.join(yt.youtube_channel_dir_path, "youtube_links.txt"), "w+", encoding="utf-8") as f:
            f.write("\n".join(youtube_links))

    elif task == "Get Transcript":
        video_id = questionary.text("Video ID:").ask()

        video_details = yt.video_details(video_id)

        yt.download_srt_yt_dlp(video_id)

        file = f"{video_details['Title']}.en.srt"
        srt_text = yt.extract_srt_text(os.path.join(yt.youtube_channel_dir_path, "subtitles", file))

        with open(os.path.join(yt.youtube_channel_dir_path, f"{file.split('.')[0]}.json"), "w+", encoding="utf-8") as f:
            json.dump(
                {
                    "VideoId": video_details["VideoId"],
                    "Url": video_details["Url"],
                    "title": video_details["title"],
                    "Description": video_details["Description"],
                    "Transcript": srt_text,
                    "PublishedAt": video_details["PublishedAt"],
                },
                f,
                ensure_ascii=False,
                indent=4,
            )

    elif task == "Get All Transcripts By Age":
        video_age = questionary.select("Video Age", choices=["Years", "Months", "Weeks", "Days"]).ask()
        video_age_number = questionary.text(f"Number of {video_age}:").ask()

        # video_ids_file_name = f"video_ids_within_{video_age_number}_{video_age.lower()}.json"
        # if os.path.exists(os.path.join(yt.youtube_channel_dir_path, video_ids_file_name)):
        #     with open(os.path.join(yt.youtube_channel_dir_path, video_ids_file_name), "r", encoding="utf-8") as f:
        #         video_ids = json.load(f)
        #         print(f"\nFound {sum(len(obj['items']) for obj in video_ids)} videos.")
        # else:

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

    else:
        if task == "Summarize Transcript":
            youtube_channel_pages = sorted(os.listdir(os.path.join(yt.youtube_channel_dir_path, "transcripts")), key=lambda x: int(x.split("_")[1].split(".")[0]))
            page = questionary.select("Which page?", choices=youtube_channel_pages).ask()
            yt.summarize_transcripts(stage, brand, os.path.join(yt.youtube_channel_dir_path, "transcripts", page), 0)

        elif task == "Summarize All Transcripts":
            yt.mp_summarize_transcripts()

        elif task == "Upload All Transcripts":
            if brand == "allinone":
                for folder in os.listdir(os.path.join(os.getcwd(), "data")):
                    if folder == "clo3d" or folder == "md":
                        for file in os.listdir(os.path.join(os.getcwd(), "data", folder, "youtube", "channel")):
                            shutil.copy(
                                os.path.join(os.getcwd(), "data", folder, "youtube", "channel", file),
                                os.path.join(yt.youtube_channel_dir_path, f"{folder}_{file}"),
                            )

            yt.upload_transcripts()
