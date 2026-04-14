from typing import List, TypedDict, Union

# Constants from the original file
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

# Constants
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
