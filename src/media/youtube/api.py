import json
import os
import re
import sys
from datetime import datetime

import requests
from dateutil.relativedelta import relativedelta
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .constants import API_KEY


class YouTubeAPI:
    def __init__(self, azure, youtube_channel_dir_path):
        self.azure = azure
        self.youtube_channel_dir_path = youtube_channel_dir_path

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

    def extract_video_transcript_text(self, video_url: str):
        """Extract transcript from a single video using YouTubeTranscriptApi"""
        # This method would be called from the transcripts module
        pass
        # This method would be called from the transcripts module
        pass
