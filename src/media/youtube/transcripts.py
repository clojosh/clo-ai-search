import json
import os
import re
from datetime import datetime, timezone

import shortuuid
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.formatters import TextFormatter
from youtube_transcript_api.proxies import WebshareProxyConfig


class TranscriptExtractor:
    def __init__(self, azure, youtube_channel_dir_path):
        self.azure = azure
        self.youtube_channel_dir_path = youtube_channel_dir_path

    def _sanitize_search_key(self, value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_=-]", "_", value)

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

        markdown_content = transcript.get("markdown_content") or transcript.get("summary") or transcript["transcript"]
        if markdown_content == "" or len(markdown_content) < 150:
            return []

        video_id = transcript["video_id"]
        if video_id.startswith("_"):
            video_id = video_id[1:]

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
                    "source": "YouTube",
                    "created_at": transcript["published_at"],
                }
            )

        return documents

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

        updated = False

        if isinstance(transcripts, dict):
            if transcripts.get("markdown_content") is not None:
                return

            if transcripts["transcript"] is None:
                return

            try:
                if transcripts["transcript"] == "" or len(transcripts["transcript"]) < 150:
                    transcripts["markdown_content"] = ""
                else:
                    markdown_content = self.azure.openai_helper.generate_structured_transcript(transcripts["title"], transcripts["transcript"])
                    transcripts["markdown_content"] = markdown_content
                updated = True
            except Exception as e:
                print(f"\nError generating markdown content for {file_path}:")
                raise e
        else:
            # Generate markdown content for each transcript.
            for trans in transcripts:
                if trans.get("markdown_content") is not None:
                    continue

                if trans["transcript"] is None:
                    continue

                try:
                    if trans["transcript"] == "" or len(trans["transcript"]) < 150:
                        trans["markdown_content"] = ""
                    else:
                        markdown_content = self.azure.openai_helper.generate_structured_transcript(trans["title"], trans["transcript"])
                        trans["markdown_content"] = markdown_content
                    updated = True
                except Exception as e:
                    raise e

        if not updated:
            return

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

        video_id = transcript["video_id"]
        if video_id.startswith("_"):
            video_id = video_id[1:]

        chunks = transcript.get("chunks") or []
        if chunks:
            documents = []
            for chunk in chunks:
                content = chunk.get("content")
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
                        "source": chunk.get("source") or "YouTube",
                        "created_at": chunk.get("created_at") or transcript["published_at"],
                        "title_vector": self.azure.openai_helper.generate_embeddings(text=chunk.get("title") or transcript["title"]),
                        "content_vector": self.azure.openai_helper.generate_embeddings(text=content),
                    }
                )

            if documents:
                self.azure.search_client.upload_documents(documents)
            return

        markdown_content = transcript.get("markdown_content") or transcript.get("summary")
        if markdown_content is not None:
            if markdown_content == "" or len(markdown_content) < 150:
                return

        now = datetime.now(timezone.utc).isoformat()

        # created_at is set once (kept from the existing indexed document if present);
        # updated_at is refreshed on every upload.
        try:
            existing = self.azure.search_client.get_document(key=video_id)
            created_at = existing.get("created_at") or now
        except Exception:
            created_at = now

        content = markdown_content if markdown_content is not None else transcript["transcript"]
        self.azure.search_client.upload_documents(
            [
                {
                    "@search.action": "mergeOrUpload",
                    "article_id": video_id,
                    "url": transcript["url"],
                    "title": transcript["title"],
                    "content": content,
                    "content_description": transcript["description"],
                    "source": "YouTube",
                    "article_created_at": transcript["published_at"],
                    "article_updated_at": transcript["published_at"],
                    "created_at": created_at,
                    "updated_at": now,
                    "title_vector": self.azure.openai_helper.generate_embeddings(text=transcript["title"]),
                    "content_vector": self.azure.openai_helper.generate_embeddings(text=content),
                }
            ]
        )
