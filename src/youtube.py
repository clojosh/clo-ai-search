import json
import multiprocessing
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from io import BytesIO
from typing import List, TypedDict, Union

import questionary
import requests  # type: ignore
import shortuuid
import yt_dlp
from dateutil.relativedelta import relativedelta
from tqdm import tqdm
from youtube_transcript_api import YouTubeTranscriptApi

from tools.azure import Azure
from tools.misc import check_create_directory, logger, sanitize_directory_file_name

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
            "Title": response["items"][0]["snippet"]["title"],
            "Description": response["items"][0]["snippet"]["description"],
            "PublishedAt": response["items"][0]["snippet"]["publishedAt"],
        }

    def retrive_youtube_video_ids(
        self, video_age_in_years: int = 0, video_age_in_months: int = 0, video_age_in_weeks: int = 0, video_age_in_days: int = 0
    ):
        """
        Retrieves a list of youtube video ids based on the video age parameters.

        Parameters:
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
        elif int(video_age_in_months) > 0:
            # If the video age is in months, set the published after date to the first day of the month
            datetime_months = datetime.now() - relativedelta(months=int(video_age_in_months))
            published_after = datetime_months.strftime("%Y-%m-%dT%H:%M:%SZ")
        elif int(video_age_in_weeks) > 0:
            # If the video age is in weeks, set the published after date to the first day of the week
            datetime_weeks = datetime.now() - relativedelta(weeks=int(video_age_in_weeks))
            published_after = datetime_weeks.strftime("%Y-%m-%dT%H:%M:%SZ")
        elif int(video_age_in_days) > 0:
            # If the video age is in days, set the published after date to the first day of the day
            datetime_days = datetime.now() - relativedelta(days=int(video_age_in_days))
            published_after = datetime_days.strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            # If no video age parameters are provided, set the published after date to yesterday
            datetime_days = datetime.now() - relativedelta(days=1)
            published_after = datetime_days.strftime("%Y-%m-%dT%H:%M:%SZ")

        # Initialize the items list
        resp_objects: List[YoutubeAPIType] = []

        # Initialize the response objects
        object: YoutubeAPIType = {"kind": "", "etag": "", "nextPageToken": None, "items": []}

        # Loop until there are no more results
        while True:
            # Make a GET request to the youtube API
            response = requests.request(
                "GET",
                "https://www.googleapis.com/youtube/v3/search",
                params={
                    "part": "id, snippet",
                    "channelId": MD_CHANNEL_ID if self.azure.brand == "md" else CLO3D_CHANNEL_ID,
                    "key": "AIzaSyC2LupTSVApfy90Bfzq8L5AAkAawOmT0gY",
                    "publishedAfter": published_after,
                    "order": "date",
                    "maxResults": 30,
                    "pageToken": object["nextPageToken"] if "nextPageToken" in object else "",
                },
                headers={
                    "Content-Type": "application/json",
                },
            )

            # Parse the response
            object = json.loads(response.text)

            # Add the items to the items list
            resp_objects.append(object)

            # If there is no next page token, break the loop
            if "nextPageToken" not in object:
                break

        # Return the items list
        return resp_objects

    def download_srt(self, video_id: str):
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

    def extract_youtube_transcripts(self, resp_objects: List[YoutubeAPIType]) -> None:
        """
        Extracts transcripts from a youtube channel

        Args:
            resp_objects (YoutubeAPIType): The response from the youtube api
            youtube_channel_path (str): The path to save the transcripts
            page (int): The page number to be processed
        """

        transcripts = []

        for i, obj in enumerate(resp_objects):
            for v in obj["items"]:
                print("Retrieving transcript for:\n" + v["snippet"]["title"] + "\n")

                # Check if the item is a video and has a video id
                if "videoId" not in v["id"]:
                    continue

                # Check if subtitle exists, if not download it
                if not os.path.exists(os.path.join(self.youtube_channel_dir_path, "subtitles", v["snippet"]["title"] + ".en.srt")):
                    self.download_srt(v["id"]["videoId"])
                else:
                    print("Subtitle already exists")

                if not os.path.exists(os.path.join(self.youtube_channel_dir_path, "subtitles", v["snippet"]["title"] + ".en.srt")):
                    # print("No subtitles found for:\n" + v["snippet"]["title"] + "\n")
                    continue

                transcript = self.extract_srt_text(os.path.join(self.youtube_channel_dir_path, "subtitles", v["snippet"]["title"] + ".en.srt"))

                # Create a dictionary to store the video data
                video = {
                    "VideoId": v["id"]["videoId"] if v["id"]["videoId"] else shortuuid.uuid(),  # Get the video id
                    "Url": f"https://www.youtube.com/watch?v={v['id']['videoId']}",  # Get the video url
                    "Title": v["snippet"]["title"].title().replace("&#39;", "'").replace("&quot;", '"').replace("&amp;", "&"),
                    "Description": v["snippet"]["description"],  # YouTube has its own description
                    "Transcript": transcript,
                    "PublishedAt": v["snippet"]["publishedAt"],  # Get the video publish date
                }

                transcripts.append(video)

            # Save the transcripts to a json file
            with open(f"{os.path.join(self.youtube_channel_dir_path, 'transcripts', f'page_{i}')}.json", "w+", encoding="utf-8") as f:
                json.dump(transcripts, f, ensure_ascii=False, indent=4)
                transcripts = []

    def extract_youtube_playlist_transcripts(self, playlist_url):
        """Extract the transcripts of every video in a playlist and save them into a JSON file"""
        playlist_title, videos = self.extract_youtube_playlist_videos(playlist_url)
        playlist_title = sanitize_directory_file_name(playlist_title)

        transcripts = []
        for video in tqdm(videos, desc="Extractng YouTube Transcripts", colour="blue", leave=False):
            transcript = {
                "Title": video["title"],
                "Source": video["url"],
                "Transcript": YouTube.extract_video_transcript_text(video["url"]),
                "Labels": [],
                "YoutubeLinks": [video["url"]],
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
    def summarize_transcripts(env: str, brand: str, youtube_channel_transcript_dir_path: str):
        """
        Summarize all the transcripts in a file

        Args:
            env (str): The environment to use
            brand (str): The brand to use
            youtube_channel_dir_path (str): The path to the directory containing the transcripts of the youtube channel

        Returns:
            None
        """
        print("\n" + os.path.split(youtube_channel_transcript_dir_path)[1].strip())

        environment = Azure(env, brand)

        # Read the transcripts from the file
        with open(youtube_channel_transcript_dir_path, "r", encoding="utf-8") as file:
            transcripts = json.load(file)

        # Summarize each transcript
        for index, trans in enumerate(transcripts):
            try:
                summary = environment.openai_helper.generate_transcript_summary(trans["Transcript"])
                trans["Summary"] = summary
            except Exception as e:
                raise e

        with open(youtube_channel_transcript_dir_path, "w", encoding="utf-8") as file:
            json.dump(transcripts, file, ensure_ascii=False, indent=4)

    def mp_summarize_transcripts(self):
        """
        Summarize transcripts of a youtube channel in parallel

        This function will call `summarize_transcripts` on all files in `youtube_channel_dir_path`
        in parallel using 5 processes.

        See `summarize_transcripts` for more details on what is done.
        """
        # Get the list of files to process
        files = os.listdir(os.path.join(self.youtube_channel_dir_path, "transcripts"))

        # Create a list to store the parameters to pass to `summarize_transcripts`
        summarize_transcripts_params = []

        # Iterate over the files and create the parameters
        for file in files:
            summarize_transcripts_params.append(
                (self.azure.stage, self.azure.brand, os.path.join(self.youtube_channel_dir_path, "transcripts", file))
            )

        # Create a multiprocessing pool and process the files in parallel
        with multiprocessing.Pool(3) as p:
            p.starmap_async(YouTube.summarize_transcripts, summarize_transcripts_params, error_callback=lambda e: print(e))
            p.close()
            p.join()

    def upload_transcripts(self):
        for file in tqdm(
            os.listdir(os.path.join(yt.youtube_channel_dir_path, "transcripts")), desc="Uploading Transcripts", colour="green", position=0, leave=True
        ):
            with open(os.path.join(self.youtube_channel_dir_path, "transcripts", file), "r", encoding="utf-8") as f:
                transcripts = json.load(f)

            upload_transcripts = []
            for transcript in transcripts:
                if transcript["Summary"] == "" or len(transcript["Summary"]) < 100:
                    continue

                if transcript["VideoId"].startswith("_"):
                    transcript["VideoId"] = "YT" + transcript["VideoId"]

                upload_transcripts.append(
                    {
                        "@search.action": "mergeOrUpload",
                        "ArticleId": transcript["VideoId"],
                        "Source": transcript["Url"],
                        "Title": transcript["Title"],
                        "Content": transcript["Summary"],
                        "ContentDescription": transcript["Description"],
                        "CreatedAt": transcript["PublishedAt"],
                        "YoutubeLinks": [transcript["Url"]],
                        "titleVector": self.azure.openai_helper.generate_embeddings(text=transcript["Title"]),
                        "contentVector": self.azure.openai_helper.generate_embeddings(text=transcript["Summary"]),
                    }
                )

            self.azure.search_client.upload_documents(upload_transcripts)


if __name__ == "__main__":
    stage = questionary.select("Which stage?", choices=["dev", "prod"]).ask()
    brand = questionary.select("Which brand?", choices=["clo3d", "md", "allinone"]).ask()
    task = questionary.select(
        "What task?",
        choices=[
            "Get Transcript",
            "Get All Transcripts",
            "Summarize Transcript",
            "Summarize All Transcripts",
            "Upload All Transcripts",
        ],
    ).ask()

    yt = YouTube(Azure(stage, brand))

    if task == "Get Transcript":
        video_id = questionary.text("Video ID:").ask()

        video_details = yt.video_details(video_id)

        yt.download_srt(video_id)

        file = f"{video_details['Title']}.en.srt"
        srt_text = yt.extract_srt_text(os.path.join(yt.youtube_channel_dir_path, "subtitles", file))

        with open(os.path.join(yt.youtube_channel_dir_path, f"{file.split('.')[0]}.json"), "w+", encoding="utf-8") as f:
            json.dump(
                {
                    "VideoId": video_details["VideoId"],
                    "Url": video_details["Url"],
                    "Title": video_details["Title"],
                    "Description": video_details["Description"],
                    "Transcript": srt_text,
                    "PublishedAt": video_details["PublishedAt"],
                },
                f,
                ensure_ascii=False,
                indent=4,
            )

    elif task == "Get All Transcripts":
        video_age = questionary.select("Video Age", choices=["Years", "Months", "Weeks", "Days"]).ask()
        video_age_number = questionary.text(f"Number of {video_age}:").ask()

        if video_age == "Years":
            video_ids = yt.retrive_youtube_video_ids(video_age_in_years=video_age_number)
        elif video_age == "Months":
            video_ids = yt.retrive_youtube_video_ids(video_age_in_months=video_age_number)
        elif video_age == "Weeks":
            video_ids = yt.retrive_youtube_video_ids(video_age_in_weeks=video_age_number)
        elif video_age == "Days":
            video_ids = yt.retrive_youtube_video_ids(video_age_in_days=video_age_number)

        yt.extract_youtube_transcripts(video_ids)

    else:
        if task == "Summarize Transcript":
            youtube_channel_pages = sorted(
                os.listdir(os.path.join(yt.youtube_channel_dir_path, "transcripts")), key=lambda x: int(x.split("_")[1].split(".")[0])
            )
            page = questionary.select("Which page?", choices=youtube_channel_pages).ask()
            yt.summarize_transcripts(stage, brand, os.path.join(yt.youtube_channel_dir_path, "transcripts", page))

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
