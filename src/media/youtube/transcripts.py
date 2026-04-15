import json
import os

import shortuuid
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.formatters import TextFormatter
from youtube_transcript_api.proxies import WebshareProxyConfig


class TranscriptExtractor:
    def __init__(self, azure, youtube_channel_dir_path):
        self.azure = azure
        self.youtube_channel_dir_path = youtube_channel_dir_path

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
            video_data = {
                "video_id": video["id"] if video["id"] else shortuuid.uuid(),  # Get the video id
                "url": f"https://www.youtube.com/watch?v={video['id']}",  # Get the video url
                "title": video["title"].title().replace("&#39;", "'").replace("&quot;", '"').replace("&amp;", "&"),
                "description": video["description"],  # YouTube has its own description
                "transcript": transcript,
                "published_at": video["published_at"],  # Get the video publish date
            }

            transcripts.append(video_data)

            if (i + 1) % 30 == 0 or (i + 1) == len(videos):
                # Save the transcripts to a json file every 30 videos to avoid losing data if the process is interrupted
                page_number = (i + 1) // 30 if (i + 1) % 30 == 0 else ((i + 1) // 30) + 1
                with open(f"{os.path.join(self.youtube_channel_dir_path, 'transcripts', f'page_{page_number}')}.json", "w+", encoding="utf-8") as f:
                    json.dump(transcripts, f, ensure_ascii=False, indent=4)

                transcripts = []

    def extract_youtube_playlist_transcripts(self, playlist_url: str):
        """Extract the transcripts of every video in a playlist and save them into a JSON file"""
        # This would be implemented in the main module
        pass

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
        if transcript["transcript"] == "" or transcript["transcript"] is None:
            print(f"\nTranscript: {transcript.get('title', '')} is missing transcript. Skipping upload.")
            return

        if "summary" in transcript:
            if transcript["summary"] == "" or len(transcript["summary"]) < 150:
                return

        if transcript["video_id"].startswith("_"):
            transcript["video_id"] = transcript["video_id"][1:]

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
