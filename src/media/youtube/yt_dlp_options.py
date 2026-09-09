import os
import re
import shutil
import subprocess


YOUTUBE_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def get_youtube_cookie_path() -> str | None:
    cookie_path = os.path.join(os.getcwd(), "youtube.com_cookies.txt")
    return cookie_path if os.path.exists(cookie_path) else None


def _runtime_version(command: str) -> tuple[int, ...] | None:
    try:
        result = subprocess.run(
            [command, "--version"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except (OSError, subprocess.CalledProcessError):
        return None

    version_match = re.search(r"\d+(?:\.\d+)+", result.stdout)
    if not version_match:
        return None

    version_text = version_match.group(0)
    version_parts = []
    for part in version_text.split("."):
        if not part.isdigit():
            break
        version_parts.append(int(part))

    return tuple(version_parts) if version_parts else None


def _find_deno() -> str | None:
    deno_path = shutil.which("deno")
    if deno_path:
        return deno_path

    winget_packages = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages")
    if not os.path.isdir(winget_packages):
        return None

    for root, _, files in os.walk(winget_packages):
        if "deno.exe" in files and "DenoLand.Deno" in root:
            return os.path.join(root, "deno.exe")

    return None


def supported_js_runtime_configs() -> dict[str, dict[str, str]]:
    deno_path = _find_deno()
    if deno_path and (_runtime_version(deno_path) or ()) >= (2, 3, 0):
        return {"deno": {"path": os.path.dirname(deno_path)}}

    node_path = shutil.which("node")
    if node_path and (_runtime_version(node_path) or ()) >= (22, 0, 0):
        return {"node": {"path": node_path}}

    return {}


def supported_js_runtimes() -> list[str]:
    return [f"{runtime}:{config['path']}" for runtime, config in supported_js_runtime_configs().items()]


def youtube_yt_dlp_options() -> dict:
    opts = {
        "http_headers": YOUTUBE_HTTP_HEADERS,
        "extractor_args": {
            "youtube": {
                "player_client": ["web", "web_safari", "web_embedded"],
            },
        },
        "extractor_retries": 3,
        "fragment_retries": 3,
        "retries": 3,
        "sleep_interval": 2,
        "max_sleep_interval": 5,
    }

    cookie_path = get_youtube_cookie_path()
    if cookie_path:
        opts["cookiefile"] = cookie_path

    js_runtimes = supported_js_runtime_configs()
    if js_runtimes:
        opts["js_runtimes"] = js_runtimes
        opts["remote_components"] = ["ejs:npm"]

    return opts


def print_youtube_block_hint(error: Exception) -> None:
    message = str(error)
    print(f"\nError: {message}")

    if "403" in message or "Forbidden" in message:
        print(
            "\nYouTube returned HTTP 403. This is usually an upstream access block, "
            "not a local parsing error. Update yt-dlp with its default extras "
            '(for example: pip install -U "yt-dlp[default]"), refresh '
            "youtube.com_cookies.txt from a logged-in browser session, then retry "
            "after a short cooldown. If challenge solving fails, install Deno 2.3+ "
            "or Node 22+."
        )
