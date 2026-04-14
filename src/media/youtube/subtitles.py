import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import torch
import yt_dlp
from faster_whisper import WhisperModel

# Global model initialization
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
model = WhisperModel(
    "large-v3",
    device=DEVICE,
    compute_type="float16",  # or "int8_float16" if VRAM constrained
)


class SubtitleManager:
    def __init__(self, azure, youtube_channel_dir_path):
        self.azure = azure
        self.youtube_channel_dir_path = youtube_channel_dir_path

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
