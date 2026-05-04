import json
import logging
import os
import re
from urllib.parse import urljoin

import html2text
import requests  # type: ignore
import shortuuid
import tiktoken
from bs4 import BeautifulSoup
from lingua import Language, LanguageDetectorBuilder
from rich import print


def logger(title: str = "", text: str = "") -> None:
    """
    Logs a message.

    Args:
        title (str, optional): The title of the message. Defaults to "".
        text (str, optional): The text of the message. Defaults to "".
    """
    if title and text:
        # Log the title and text in green color
        logging.info("\033[92m" + title + "\033[0m" + "\n" + str(text))
    elif title:
        # Log the title in green color
        logging.info("\033[92m" + title + "\033[0m")
    else:
        # Log the text
        logging.info(str(text))


def num_tokens_from_string(string: str, model: str = "gpt-4") -> int:
    """Returns the number of tokens in a text string. https://github.com/openai/openai-cookbook/blob/main/examples/How_to_count_tokens_with_tiktoken.ipynb"""
    encoding = tiktoken.encoding_for_model(model)
    num_tokens = len(encoding.encode(string))
    return num_tokens


def remove_html_tags(html_string: str) -> str:
    """
    Removes all HTML tags from a string, with the exception of <img> tags
    that meet the following criteria:
    1. The tag must have a 'src' attribute.
    2. The 'src' attribute value must start with 'http://' or 'https://'.

    Preserved <img> tags are kept entirely.

    Args:
        html_string: The input string containing HTML markup.

    Returns:
        The string with all non-qualifying HTML tags removed.
    """

    def tag_replacer(match) -> str:
        tag = match.group(0)
        tag_lower = tag.lower()

        # 1. Check for non-image tags (remove them)
        # This also removes closing </img> tags.
        if not tag_lower.startswith("<img"):
            return ""

        # Now we know it's an opening or self-closing <img> tag.

        # Regex to find the src attribute value (case-insensitive search for 'src').
        # This handles both single and double quotes for the attribute value.
        src_match = re.search(r'src=["\'](.*?)["\']', tag, re.IGNORECASE)

        if not src_match:
            # If no src attribute is found, remove the tag (e.g., malformed <img> or <img> without src)
            return ""

        src_value = src_match.group(1)

        # 2. Check preservation rules

        # Rule 2: Must start with http or https
        is_external_url = src_value.lower().startswith("http://") or src_value.lower().startswith("https://")

        if not is_external_url:
            # Remove if it fails any preservation rule (is not an external URL).
            return ""

        # Rule 3: Excluded urls
        excluded_urls = ["https://support.clo3d.com/hc/article_attachments/39086046335129"]
        for excluded_url in excluded_urls:
            if src_value == excluded_url:
                return ""

        return tag

    # The regex pattern matches any HTML tag structure:
    # < followed by one or more characters that are not >, ending with >
    cleaned_string = re.sub(r"<[^>]+>", tag_replacer, html_string)
    return cleaned_string


def remove_miscellaneous_text(article):
    misc_list = ["Go back to the List of Contents"]

    for misc in misc_list:
        article = article.replace(misc, "")

    # For FAQ articles, remove the Question and word "Answer"
    question_starting_index = article.find("Question")
    answer_ending_index = article.find("Answer")
    if question_starting_index != -1 and answer_ending_index != -1:
        article = article[answer_ending_index + 6 :]

    return article.strip()


def trim_tokens(article: str):
    """
    This function takes in a string article and trims it by removing unwanted characters.

    The following steps are performed:
    1. Replaces all occurrences of "\u00a0" with a single space.
       This is done to remove non-breaking spaces, which can appear in HTML strings.
    2. Replaces all occurrences of "&nbsp" with a single space.
       This is done to remove HTML non-breaking spaces, which can appear in HTML strings.
    3. Replaces all occurrences of non-word, non-digit, non-whitespace characters with an empty string.
       This is done to remove all special characters, punctuation, and other unwanted characters from the string.
    4. Replaces all occurrences of one or more whitespace characters with a single space.
       This is done to remove all extra whitespace from the string.
    5. Replaces all occurrences of one or more newline characters with a single newline character.
       This is done to remove all extra newline characters from the string.
    6. Strips any leading or trailing whitespace from the string.

    The resulting string is returned.
    """
    # article = article.replace("\u00a0", " ").replace("&nbsp", " ")
    # article = re.sub(r"[^\w0-9-\s\n_*.`~!@#$%^&()+={}\:\"'?/><,/+\[\]]", "", article)
    # article = re.sub(r"\n+", "\n", article)
    article = re.sub(r"\s{2,}", " ", article)

    return article.strip()


def clean_html(html_content: str) -> BeautifulSoup:
    """
    This function takes in an HTML string and returns a BeautifulSoup object
    with non-content elements removed. Non-content elements are often found
    in web pages and include things like scripts, styles, navigation bars,
    footers, headers, aside elements, and forms. These elements are removed
    to leave only the content of the web page.

    The function works by first parsing the HTML string into a BeautifulSoup
    object. It then loops through all the non-content tags in the soup and
    decomposes them, effectively removing them from the soup.

    The resulting soup object contains only the content elements of the
    web page, making it easier to extract the relevant information from the
    page.

    Parameters:
    html_content (str): The HTML string to be cleaned

    Returns:
    BeautifulSoup: The cleaned HTML soup object
    """

    soup = BeautifulSoup(html_content, "html.parser")

    # Remove non-content elements often found in web pages
    for tag in soup.find_all(["script", "style", "nav", "footer", "header", "aside", "form"]):
        # Decompose the tag, effectively removing it from the soup
        tag.decompose()

    return soup


def html_to_markdown_converter(html_content: str) -> str:
    """
    Converts an HTML string into a Markdown string, replacing YouTube
    iframes with direct Markdown links.
    """
    soup = BeautifulSoup(html_content, "html.parser")

    # Find all figures that contain iframes (common for embedded media)
    for figure in soup.find_all("figure"):
        data_oembed_url = figure.find("div")
        if data_oembed_url:
            data_oembed_url = data_oembed_url.get("data-oembed-url", "")
            if "youtube.com" in data_oembed_url:
                video_url = data_oembed_url

                # Create a new anchor tag or just replace with text
                link_text = f"YouTube Video: {video_url}"
                figure.replace_with(link_text)

    # Find all iframes that point to YouTube
    for iframe in soup.find_all("iframe", src=re.compile(r"youtube\.com/embed/")):
        video_url = iframe["src"]

        # Convert embed URL to a standard watch URL (optional but cleaner)
        video_url = video_url.replace("embed/", "watch?v=")

        # Create a new anchor tag or just replace with text
        link_text = f"YouTube Video: {video_url}"
        iframe.replace_with(link_text)

    # Get the modified HTML back as a string
    modified_html = str(soup)

    # Create and configure the converter
    h = html2text.HTML2Text()
    h.ignore_links = False
    h.ignore_images = False
    h.body_width = 0
    h.unicode_snob = True
    h.backquote_code_style = True

    # Perform the conversion
    markdown_content = h.handle(modified_html)

    return markdown_content.strip()


def remove_unwanted_markdown_images(markdown_content: str):
    markdown_data_image_regex = r"!\[_Divider.png\]\(.*?\)"
    data_images = re.findall(markdown_data_image_regex, markdown_content)

    for data_image in data_images:
        markdown_content = markdown_content.replace(data_image, "")

    return markdown_content


def replace_base64_images_with_placeholders(markdown_content: str):
    inline_images = []
    markdown_data_image_regex = r"!\[.*?\]\(data:image.*?\)"
    data_image_regex = r"\(data:image.*?\)"

    data_images = re.findall(markdown_data_image_regex, markdown_content)
    for data_image in data_images:
        data_image_match = re.search(data_image_regex, data_image)
        if not data_image_match:
            continue

        data_image_src = data_image_match.group(0).replace("(", "").replace(")", "")
        alt = f"image_{shortuuid.uuid()}.png"
        placeholder = f"[IMAGE:{alt}]"
        inline_images.append({"PlaceHolder": placeholder, "source": data_image_src, "Alt": alt, "Width": "", "Height": ""})

        markdown_content = markdown_content.replace(data_image, placeholder)

    return markdown_content, inline_images


def extract_youtube_links(article: str):
    """Return a list of youtube links from an article"""

    iframe_regex = r"<iframe(?:\stitle=\"[a-zA-Z0-9\s]*\")? src=\"[^\s]*\""
    iframes = re.findall(iframe_regex, article)

    youtube_links = []
    for iframe in iframes:
        id = iframe.split("/")[-1].split("?")[0].replace('"', "")
        youtube_links.append("https://www.youtube.com/watch?v={}".format(id))

    return youtube_links


def check_create_directory(path):
    if not os.path.exists(path):
        os.makedirs(path)


def sanitize_directory_file_name(text):
    return re.sub(r'[\\/*?:"<>|]', "", text)


def get_section_and_category(env, section_id):
    section_response = requests.request(
        "GET",
        env.get_zendesk_article_section_api_endpoint(section_id),
        headers={
            "Content-Type": "application/json",
        },
    )
    section_objects = json.loads(section_response.text)

    category_response = requests.request(
        "GET",
        env.get_zendesk_article_category_api_endpoint(section_objects["section"]["category_id"]),
        headers={
            "Content-Type": "application/json",
        },
    )
    category_objects = json.loads(category_response.text)

    return (
        section_objects["section"]["id"],
        section_objects["section"]["name"],
        category_objects["category"]["id"],
        category_objects["category"]["name"],
    )


def check_image_exists(url):
    """
    Performs a HEAD request to check if the URL returns a 200 status code
    and indicates an image content type.
    """
    try:
        # Use a HEAD request to fetch only the headers, which is faster
        # and conserves bandwidth compared to a full GET request.
        response = requests.head(url, timeout=5, allow_redirects=True)

        # 1. Check if the HTTP status code is OK (200)
        if response.status_code != 200:
            print(f"Error: URL returned status code {response.status_code} for {url}")
            return False

        # 2. Check the Content-Type header
        content_type = response.headers.get("Content-Type", "")

        # Check if the content type indicates an image (e.g., 'image/jpeg', 'image/png')
        if content_type.startswith("image/"):
            # print(f"Success: URL is a valid image ({content_type}) for {url}")
            return True
        else:
            # print(f"Error: Content type is '{content_type}', not an image for {url}")
            return False

    except requests.exceptions.Timeout:
        print(f"Error: Request timed out for {url}")
        return False
    except requests.exceptions.ConnectionError:
        print(f"Error: Failed to connect to URL {url}. Check network or domain.")
        return False
    except requests.exceptions.RequestException as e:
        # Catch all other requests exceptions (e.g., bad URL format)
        print(f"An unexpected request error occurred for {url}: {e}")
        return False
    except Exception as e:
        # Catch any other unforeseen exceptions
        print(f"An unknown error occurred for {url}: {e}")
        return False


def ensure_absolute_markdown_urls(markdown_text: str, base_url: str) -> str:
    """
    Ensures all links in a Markdown string have an absolute base URL.

    Args:
        markdown_text: The input Markdown string.
        base_url: The base URL to use for relative links (e.g., "https://example.com/").

    Returns:
        The Markdown string with absolute URLs.
    """
    # Regex to find all Markdown links: [link_text](url)
    # The groups capture:
    # 1: The link text
    # 2: The URL
    link_regex = re.compile(r"\[([^\[\]]+)\]\(([^)]+)\)")

    def replacer(match):
        link_text = match.group(1)
        original_url = match.group(2)

        # Use urljoin to resolve the link against the base_url.
        # urljoin intelligently handles absolute URLs, protocol-relative links (//),
        # and relative links, making them absolute using the base_url.
        absolute_url = urljoin(base_url, original_url)

        # Reconstruct the Markdown link with the absolute URL
        return f"[{link_text}]({absolute_url})"

    return link_regex.sub(replacer, markdown_text)


def get_version_info_by_article_id(article_id, json_data):
    """
    Searches for an article_id and returns the version and year.
    """
    target_id = str(article_id)

    for entry in json_data:
        raw_version_string = entry.get("version", "")
        features = entry.get("features", [])

        # 1. Extract Version: Look for digits and dots at the start
        # e.g., "2025.2"
        v_match = re.search(r"(\d+\.\d+)", raw_version_string)
        version_num = v_match.group(1) if v_match else raw_version_string

        # 2. Extract Year: Look for any 4-digit number inside parentheses
        # e.g., "(... 2025)"
        y_match = re.search(r"\(.*(\d{4}).*\)", raw_version_string)
        year = y_match.group(1) if y_match else None

        # 3. Search Features
        for feature in features:
            url = feature.get("url")
            if url:
                # Matches the numeric ID in the URL path
                id_match = re.search(r"/articles/(\d+)", url)
                if id_match and id_match.group(1) == target_id:
                    return {"version": version_num, "year": year}

    return None


def lingua_language_detector(text, print_output: bool = True):
    """
    Detects the language in the given text and returns the language name and code i.e Language.ENGLISH, en-us

    Args:
        text (str): The text string to detect the language from.
        print_output (bool, optional): Whether to print the detected language and confidence values. Defaults to True.

    Returns:
        Tuple[str, str]: The detected language name and code (e.g. English, en-us)
    """
    # List of supported languages
    languages = [Language.ENGLISH, Language.KOREAN, Language.CHINESE, Language.JAPANESE, Language.SPANISH, Language.PORTUGUESE, Language.FRENCH]

    # Build the language detector
    detector = LanguageDetectorBuilder.from_languages(*languages).build()

    # Detect the language
    language_detected = detector.detect_language_of(text)
    if language_detected is None:
        return None, None

    # Return the detected language name and code
    return language_detected.name.title(), language_detected.iso_code_639_1.name.lower()


if __name__ == "__main__":
    html = """<p> </p><h2 id=\"h_01KA8B60XJEBG4F3HK276F48GQ\"><span style=\"color: #2F3941;\"><strong>New Feature Overview</strong></span></h2><h4 id=\"h_01KA89VA77PR4GF0MWGWV1AJ27\"><span style=\"color: #2F3941;\"><strong>Marvelous Designer 2025.2</strong></span></h4><figure class=\"wysiwyg-media\"><div data-oembed-url=\"https://www.youtube.com/watch?v=mNiP-vMqExs\"><iframe src=\"//www.youtube-nocookie.com/embed/mNiP-vMqExs\" frameborder=\"0\" allowfullscreen=\"\" allow=\"encrypted-media\" style=\"width: 100%; aspect-ratio: 16 / 9;\"></iframe></div></figure><h4 id=\"h_01KA89X7EH0DVQ2TVZAGPXKSDC\"> </h4><h4 id=\"h_01KA89YECD4Y4MT4ZYN66YM7W6\"><span style=\"color: #2F3941;\"><strong>Marvelous Designer 2025.1</strong></span></h4><figure class=\"wysiwyg-media\"><div data-oembed-url=\"https://www.youtube.com/watch?v=zL9RcGpJG7E\"><iframe src=\"//www.youtube-nocookie.com/embed/zL9RcGpJG7E\" frameborder=\"0\" allowfullscreen=\"\" allow=\"encrypted-media\" style=\"width: 100%; aspect-ratio: 16 / 9;\"></iframe></div></figure><h3 id=\"h_01K339Z1H5079K01P4VY79KQCN\"> </h3><h4 id=\"01HYPBZ32PHQDM3V83HTJQAV8G\"><span style=\"color: #2F3941;\"><strong>Marvelous Designer 2025.0</strong></span></h4><h3 id=\"h_01K339Z1H5DB49SGH66SNEQCVG\"><iframe src=\"https://www.youtube.com/embed/7PDicby-uXY\" width=\"560\" height=\"315\" frameborder=\"0\" allowfullscreen=\"allowfullscreen\" allow=\"accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share\"></iframe></h3><hr><p> </p><h2 id=\"h_01KA8B60XJTJACKNJ0VW1SK0A7\"><span style=\"color: #2F3941;\"><strong>Key Features</strong></span></h2><h4 id=\"h_01K0TXNKJ8JACMAY8NH9XSGECN\"><span style=\"color: #2F3941;\"><strong>2025.2 (November 2025)</strong></span></h4><ul>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e6a5a10bc862bfa4d6b8f81c5a62ba7ac\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/51753492522265-Pattern-Object-Type-Conversion\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Convert Patterns to Trims and Accessories</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e1afeabb3c3a5e448e69472b0f478ac02\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/51752244831897-MetaHuman-DNA-Importer\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">MetaHuman DNA Importer</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e14026de79654dd9d9cacd2cc10884c49\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358157019161-Keyframe-Animation-ver-2025-0\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Keyable Properties Expansion</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e9e4765bde465d1b2974a315a4d1cfb9f\"><a href=\"https://support-connect.clo-set.com/hc/en-us/sections/44750762795417-Export\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">EveryWear Export with FBX</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"eaca9e557c34665291ea7b1454af50f43\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/51752931944857-Pleats\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Pleat Creation Tool</span></span></a></li>\n</ul><p> </p><h4 id=\"h_01KA8B60XJG1406SD6DHD19W5S\"><span style=\"color: #2F3941;\"><strong>2025.1 (August 2025)</strong></span></h4><ul>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e31f3b93f020ee4917cc9b844cc09ba15\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358252183833\" target=\"_blank\" rel=\"noopener noreferrer\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Off-Avatar 3D Pen Drawing</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e95af796320870832c0f21a84791ce7cd\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358335130649\" target=\"_blank\" rel=\"noopener noreferrer\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Keep Topology after Auto Fitting</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"ee9a961ec51d3e91da6d34e86ef2bbefe\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/48920347302297\" target=\"_blank\" rel=\"noopener noreferrer\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Pattern Drafter</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"ebcf17f4b6eb2c291465350e926acd414\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358263976729\" target=\"_blank\" rel=\"noopener noreferrer\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Soft Body for Custom Avatars</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e48ebd1328ef1b907ca047d7183a6a588\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/48920020621849\" target=\"_blank\" rel=\"noopener noreferrer\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Denim Wet Wash</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"ee6ddd17f8ac5b8370c7664982324b205\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/48920289920537\" target=\"_blank\" rel=\"noopener noreferrer\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">AI Pose Generator (Beta)</span></span></a></li>\n</ul><h4 id=\"h_01K0TXWMTD1Q93VCMA4SMWZBER\">\n<br><span style=\"color: #2F3941;\"><strong>2025.0 (April 2025)</strong></span>\n</h4><ul>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e732c267e28bcb0e30a8f11f402e147b2\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358126161177\" target=\"_blank\" rel=\"noopener noreferrer\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">UI Improvement</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"ef4a61a42b3dfde14f2e7b7b9417cdeda\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358157019161/\" target=\"_blank\" rel=\"noopener noreferrer\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Keyframe Animation</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e8e922e86d605b748cc85cd3e244d9a4e\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358367597081\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Quad Mesh Improvement</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"eedbe0c33417c6e95c61c0305d6723f9c\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358133151385/\" target=\"_blank\" rel=\"noopener noreferrer\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">IK Joint Mapping</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e5473f0a520f787c869c188368fb72072\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358238232985/\" target=\"_blank\" rel=\"noopener noreferrer\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Side and Back UV Expansion</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e4ef0c0e89be02e556a3956ef0a0fbbc9\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/44136743366681\" target=\"_blank\" rel=\"noopener noreferrer\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">[Beta] Fur Strand in 3D Window</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"ed2142a20f9c15b45372758faa65b3eb5\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358158495129\" target=\"_blank\" rel=\"noopener noreferrer\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Auto Convert to Motion</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e6ae15e4605e3e1f6644c2d293c34fd13\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358157799961\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Modular Library</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"eadbbd903daf2f5b7ea00e625661cad3d\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358127622809\" target=\"_blank\" rel=\"noopener noreferrer\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">New Library Window</span></span></a></li>\n</ul><hr><p> </p><h2 id=\"h_01KA8AEA6NDYSG1E562T8TQDHT\"><span style=\"color: #2F3941;\"><strong>Effortless &amp; Intuitive</strong></span></h2><h4 id=\"h_01K0TY11NQQRXPBSEY3WZNYEMK\"><span style=\"color: #2F3941;\"><strong>2025.2 (November 2025)</strong></span></h4><ul>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"ea58174c3026f3e3100be479d553a1eba\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358288593177-ZIPPER-Zipper-Slider-Right-Click-Pop-Up-Menu\"><span style=\"color: #2F3941;\"><span data-sheets-root=\"1\"><span class=\"wysiwyg-underline\">Zipper Improvement</span></span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"efaba8d3214b34ddb2256ed780486ca00\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/48920347302297-Pattern-Drafter-Beta\"><span style=\"color: #2F3941;\"><span data-sheets-root=\"1\"><span class=\"wysiwyg-underline\">Add Pants and Skirts in Pattern Drafter</span></span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"eda0f28f80e95b0388aedcf411a59adb5\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358263976729-Avatar-Properties\"><span style=\"color: #2F3941;\"><span data-sheets-root=\"1\"><span class=\"wysiwyg-underline\">Soft Body Simulation in GPU</span></span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e047601dd518781694d0b0fbfae703dc7\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358157019161-Keyframe-Animation-ver-2025-0\"><span style=\"color: #2F3941;\"><span data-sheets-root=\"1\"><span class=\"wysiwyg-underline\">Animation Layer Setup</span></span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e0ee46f929fd14455a53d36a5f3238661\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358232630937-Simulation\"><span style=\"color: #2F3941;\"><span data-sheets-root=\"1\"><span class=\"wysiwyg-underline\">Instant Simulation Stop</span></span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e9f4acef9d46dd28df17d4581dea94a45\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/49694260744089-AI-Studio-Plug-In-Activation-Enterprise-License-Only\"><span style=\"color: #2F3941;\"><span data-sheets-root=\"1\"><span class=\"wysiwyg-underline\">AI Feature Activation</span></span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"ecd75ca2357114ea2c0d4da1337834763\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358146159769-Pins\"><span style=\"color: #2F3941;\"><span data-sheets-root=\"1\"><span class=\"wysiwyg-underline\">Pin Group Selection</span></span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e1c4d12d3e7846afcb4706facc3383a10\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358169509273-User-Interface-Above-ver4-2-0\"><span style=\"color: #2F3941;\"><span data-sheets-root=\"1\"><span class=\"wysiwyg-underline\">UI Color Customization</span></span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e0fdf418dad72fc7beb5f5c95c4734182\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/51757259179545-ZIPPER-Edit-Zippers\"><span style=\"color: #2F3941;\"><span data-sheets-root=\"1\"><span class=\"wysiwyg-underline\">Add Edit Zipper Tool</span></span></span></a></li>\n</ul><h4 id=\"h_01KA8B60XJ24P2JBQ3P7CGEKRC\"> </h4><h4 id=\"h_01KA8B60XJDEV03G6MQJRN8EXY\"><span style=\"color: #2F3941;\"><strong>2025.1 (August 2025)</strong></span></h4><ul>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e542f19372e7fe29fa75753aadc72d3df\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/48920706512665\" target=\"_blank\" rel=\"noopener noreferrer\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Zipper Style</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e82842a225f09849337b23096a95990bd\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/48977895500825\" target=\"_blank\" rel=\"noopener noreferrer\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Group Internal Lines and Shapes</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"ed865c0090e80b763fe30f125f8ee8961\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358259548953\" target=\"_blank\" rel=\"noopener noreferrer\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Trim Mirror Paste</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"ea272410abcd7dfa986d55d863a8600b5\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/49132239080857\" target=\"_blank\" rel=\"noopener noreferrer\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Blend Print with Fabric</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"ea892bd99f0e624817006427d622bf7b8\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358200025497\" target=\"_blank\" rel=\"noopener noreferrer\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Show Wireframe for Avatars</span></span></a></li>\n</ul><h4 id=\"h_01K0TY1MB7Q2PWV7MJ4R41TQN2\">\n<br><span style=\"color: #2F3941;\"><strong>2025.0 (April 2025)</strong></span>\n</h4><ul>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e526681d2826cf42d2f8e04a04ade03be\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358149166233\" target=\"_blank\" rel=\"noopener noreferrer\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Schematic Render</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e010bcc0ec2492c62c30b862ddfb02526\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358149073305\" target=\"_blank\" rel=\"noopener noreferrer\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Auto Sewing</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"ed2a13f98d1801e134612d88f13e8670c\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358146871065\" target=\"_blank\" rel=\"noopener noreferrer\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Blend Graphic with Fabric</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e0ef62b9ab445006def0f45dead77b68d\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358145163033\" target=\"_blank\" rel=\"noopener noreferrer\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Garment Fit Properties</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"ea0698f0ba7753519f906e41152f7785d\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358171722265\" target=\"_blank\" rel=\"noopener noreferrer\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Object Browser</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e620166bc20f714546a6579efa1783bb2\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358364198425\" target=\"_blank\" rel=\"noopener noreferrer\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">PBR Map Improvement</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e9b0a301aca013ce1033fe8edf6e3d259\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358449159961\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Smooth Corner for Topstitch</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e0f8cc7d3a4f9506308a559d074d2308e\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358442871705\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Offset Internal Line Improvement</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e8743d93b96418e448bc0fbf12c62b693\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358128221337\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Avatar Editor Improvement</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"ebf40a5d7efe36e5f24bbb95530f94558\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358157097881\" target=\"_blank\" rel=\"noopener noreferrer\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Custom UI Layout</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e78fc2ee1c4bf94e9b79e0b458663f37a\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358435037081\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">IK Mode for Avatar Joint</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e7ac84a6d8dd90ccd1728d610a4012669\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358335130649\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Auto Fitting Improvement</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"ea84051b84e59771b15d541911434f747\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358119536665\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Load all Texture Maps</span></span></a></li>\n</ul><hr><h3 id=\"h_01JSJDHT1TAMSNK45P8VDK1DS6\"> </h3><h2 id=\"h_01KA8B60XK97DRG42A8FSPSXJB\"><span style=\"color: #2F3941;\"><strong>All-In-One</strong></span></h2><h4 id=\"h_01K0TXSTRC5KY56M5VD6ZTHC0T\"><span style=\"color: #2F3941;\"><strong>2025.2 (November 2025)</strong></span></h4><ul>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e492155f6997db738610e342a9dfc725d\"><a href=\"https://support-connect.clo-set.com/hc/en-us/articles/45304301912857-Garment-Tab\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">[EveryWear] Target Joint Edit Mode </span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"eae006b4e73a1268bd6e7ecf18144ce02\"><a href=\"https://support-connect.clo-set.com/hc/en-us/articles/45304301912857-Garment-Tab\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">[EveryWear] Mirror Weights</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e1304c7d21f02d35f5f6b47e2a547775b\"><a href=\"https://support-connect.clo-set.com/hc/en-us/articles/45304301912857-Garment-Tab\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">[EveryWear] Switch to Bind Pose</span></span></a></li>\n</ul><h4 id=\"h_01KA8B60XK4YPPYN8Y0XTTNVHJ\"> </h4><h4 id=\"h_01KA8B60XKZT83JE90Q9XAW6GC\"><span style=\"color: #2F3941;\"><strong>2025.0 (April 2025)</strong></span></h4><ul>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e706ce955f27ec38e8fd9351b0994befe\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358157142297\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Sculpt Tool</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"edc55ad531c51ec4693e14d120e4220db\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358146261913#h_01JSPDGAEMWWYTBAFFW1KZXRPC\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Animation with Trim Simulation</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e410c40e81eb61737b768ecfcd79f39b2\"><a href=\"https://connect-hc.zendesk.com/hc/en-us/articles/45304301912857\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Auto Seal</span></span></a></li>\n</ul><hr><p> </p><h2 id=\"h_01KA8GSKE096BMZ5761BKVKDJJ\"><span style=\"color: #2F3941;\"><strong>Boost Assets</strong></span></h2><h4 id=\"h_01K0TXXVDTQPACJ0FBCB4MW9F9\"><span style=\"color: #2F3941;\"><strong>2025.2 (November 2025)</strong></span></h4><ul><li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"ed8a9a7f1b63b3f077d1c4385c1606dae\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358145246361-Register-Accessory-Hair-Shoes-Glasses-Earring-zacs-Ver-2025-0\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Added \"Hat\" as an Accessory Category</span></span></a></li></ul><p> </p><h4 id=\"h_01KA8B60XK9VK4GPGQD9SYG0P9\"><span style=\"color: #2F3941;\"><strong>2025.1 (August 2025)</strong></span></h4><ul><li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e4959db72de7898b34f75d02721980a35\"><span style=\"color: #2F3941;\">AI Studio - Added an icon in the top right corner.</span></li></ul><p> </p><h4 id=\"h_01K0TXV1N3A6995MAHNZHDAMZ0\"><span style=\"color: #2F3941;\"><strong>2025.0 (April 2025)</strong></span></h4><ul>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"efed46e9946990837dad77d69407dd449\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358259456665\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Two-way Zippers</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"ec1d2816759db8669c64f29df72668183\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358145246361\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Register Accessory</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" id=\"01JSJDRCFEZKP926A9AA50T37Z\" data-list-item-id=\"e8efd14408fdd371b38d9e56b1ffc22f8\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358231940889\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">OBJ Zipper Teeth with Preset</span></span></a></li>\n</ul><hr><p> </p><h2 id=\"h_01JSJERWW363311TXF0A0NXMZX\"><span style=\"color: #2F3941;\"><strong>Seamless Workflow</strong></span></h2><h4 id=\"h_01K0TXYEGAS412SNFK44T86XDB\"><span style=\"color: #2F3941;\"><strong>2025.2 (November 2025)</strong></span></h4><ul><li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e0d00f15d17156cb5bff47cee45daaf2b\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358235084313-UV-EDITOR-MODE-Edit-UV-location\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Select All with Same Property in UV Editor</span></span></a></li></ul><p> </p><h4 id=\"h_01KA8B60XKA3JRPHFFZ4632XDN\"><span style=\"color: #2F3941;\"><strong>2025.1 (August 2025)</strong></span></h4><ul><li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"ed029878859a2026f1b39672fb1e504e1\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358279381529\" target=\"_blank\" rel=\"noopener noreferrer\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Save Selected Garment</span></span></a></li></ul><p> </p><h4 id=\"h_01K0TXVJQ15QZB1X9ES2YANE9Z\"><span style=\"color: #2F3941;\"><strong>2025.0 (April 2025)</strong></span></h4><ul>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" id=\"h_01JSJDJMWYEJ13YMZ3FYAVWR15\" data-list-item-id=\"e48006fd8233473e4b32cd3ac55c5e000\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358364198425\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Color Switch (Graphics &amp; Textures)</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"edb0fc59593baaaea63f8cfa5ff0ea08d\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358232885017\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Export FBX with Material Names</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"ee9a4dee8769b125a529816f926fb02cd\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358112803225\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Polygon Optimization for Topstitch</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e75d7be10fd6a7865e4ed287109e493f5\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358199862553\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Support for USDZ file types</span></span></a></li>\n</ul><hr><p> </p><h2 id=\"h_01JSJDRGDQ7JA3D46CGRF0J8RV\"><span style=\"color: #2F3941;\"><strong>Other Features</strong></span></h2><h4 id=\"h_01K0TXYKEC3SEVHT4TY8DS4Y1D\"><span style=\"color: #2F3941;\"><strong>2025.2 (November 2025)</strong></span></h4><ul>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"ecdfbf7db58ed25a685301f36f09dda2e\"><span style=\"color: #2F3941;\">[Library] Docked widget for universal browser</span></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"ea1d337e9cd4220616a2e5dbd96ef8018\"><span style=\"color: #2F3941;\">Seamline Sync during Export</span></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"eddfca7600999f088be60c2c1d8c9f29c\"><span style=\"color: #2F3941;\">Change the Script menu to Plugins.</span></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"eba9f02e2c2797bf33d2b014aa5cae328\"><span style=\"color: #2F3941;\">[EveryWear] Add \"Beta\" and Change to Plug-In </span></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e507b51d02d4196355f1387b35daadc8f\"><span style=\"color: #2F3941;\">[3D] Improve Wind Gizmo Rotation Direction</span></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"eed73946afa6ad6feb3d04cf58a71a24c\"><span style=\"color: #2F3941;\">[Avatar] Style Configurator for Kid Avatars</span></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e715e5d14f2226ad1118fa5f4a8e5a37c\"><span style=\"color: #2F3941;\">[Preferences] Reset all dialog with \"Don't Show Again\"</span></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"ea847ddab66b8e3527e4e65c14e88bca2\"><span style=\"color: #2F3941;\">[UI] Change the default unit to millimeters for topstitch's thread thickness</span></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"ec5f9eb77ec7929968afc0641d79183b7\"><span style=\"color: #2F3941;\"><span data-sheets-root=\"1\">[ETC] Removal of file name &amp; file path limitation to 255 characters</span></span></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"ed9598d1be3fb0f869581a25c7c0ea9fc\"><span style=\"color: #2F3941;\"><span data-sheets-root=\"1\">[Network] AutoLogout Improvement</span></span></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e0b2c32da824df31c541b0b07c61ec2e0\"><span style=\"color: #2F3941;\">[Graphic] Placement by measurement</span></li>\n</ul><p> </p><h4 id=\"h_01KA8B60XKR3C1R4Z4E75826V4\"><span style=\"color: #2F3941;\"><strong>2025.1 (August 2025)</strong></span></h4><ul>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e16f84a3a42634f163d2d4202f37aeae1\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/48920133557145\" target=\"_blank\" rel=\"noopener noreferrer\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Import and Export Presets</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e4e16505f5425bf160a92e57441f5b459\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358200025497\" target=\"_blank\" rel=\"noopener noreferrer\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Show and Hide Scene and Props</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"ea5bed5ab3106373d02bdc6c423dbf245\"><a href=\"https://support-connect.clo-set.com/hc/en-us/articles/45304301912857\" target=\"_blank\" rel=\"noopener noreferrer\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Enhaced Masking Selection</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e6048563ec7ff90a752ffb79f823eece5\"><a href=\"https://support-connect.clo-set.com/hc/en-us/articles/45304301912857\" target=\"_blank\" rel=\"noopener noreferrer\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Partial Auto Rigging</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"eadf3af773d2b8c59ff2120503db74bfd\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358157019161\" target=\"_blank\" rel=\"noopener noreferrer\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">[Animation] Clamp Range to Selected Layers</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e007ebb05e94cc673872b4da8b6397c34\"><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358149166233\" target=\"_blank\" rel=\"noopener noreferrer\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Schematic Render related improvement</span></span></a></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e81f24c089c7aa6f292299f0de33434b8\"><span style=\"color: #2F3941;\">[Sculpt] Display a warning dialog when starting the simulation</span></li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"eda61f82728ff485702ff33b373152e13\">\n<p><a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358340247193\" target=\"_blank\" rel=\"noopener noreferrer\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Flatten multiple areas as a single merged pattern</span></span></a></p>\n<p> </p>\n</li>\n</ul><h4 id=\"h_01K0TXVWS35R04235V7VSR9HW1\"><span style=\"color: #2F3941;\"><strong>2025.0 (April 2025)</strong></span></h4><ul>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"edd30e8fc38eb98c6dda43a656d437ea8\">\n<a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358413069209\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">[UV Editor]</span></span></a><span style=\"color: #2F3941;\">  -  Anti-alias Lines option added</span>\n</li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"ea5d948eba07ac2439dae2dad67a7959a\">\n<a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358200025497#01JNZC1JCRS1RWPDWFM1Z25J2R\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Show Stress Map</span></span></a><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\"> </span>- Option has been added to the 3D Display Fit Maps options</span>\n</li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"eca64053b3c4889332cb87dd83cb24007\">\n<a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358171722265\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Object Browser</span></span></a><span style=\"color: #2F3941;\"> - Now contains all the Style Windows &amp; Scene Window</span>\n</li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e7ded2c4f8d6b79870e591b00d1ac0ca7\">\n<a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358171818521\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Seamline Property</span></span></a><span style=\"color: #2F3941;\">  - Many new property types added</span>\n</li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e2856acbf3430d5a1dff877bcebce84f8\">\n<a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358449159961\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Topstitch Style Property</span></span></a><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\"> </span>- You can now create custom OBJ topstitch assets and more</span>\n</li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e4408ebd71e001552c2496881b10c8b47\">\n<a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358296348057\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">[Fabric] Physical Property Creator</span> </span></a><span style=\"color: #2F3941;\">- Information added</span>\n</li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"eabb6e16a5172dfa667f302779f203627\">\n<a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358218656921\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">[Pattern Property] Seam Taping Physical Property</span> </span></a><span style=\"color: #2F3941;\">- More material options &amp; customization added</span>\n</li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e7e4c6985b7a5183431da1ebea6bef78c\">\n<a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358377731865\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">[Property Editor] Texture</span></span></a><span style=\"color: #2F3941;\"> -  Map Options is added with Open/Add with Another App</span>\n</li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e38ddafa9b5e189df569c2b2eb7828c6f\">\n<a href=\"https://connect-hc.zendesk.com/hc/en-us/articles/45304285349273\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">EveryWear</span></span></a><span style=\"color: #2F3941;\"> - The location has been moved to CONNECT in the Main Menu</span>\n</li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e3aec53ad4f98eece60eddf8f37c89205\">\n<a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358210639897\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Auto Convert to Avatar</span></span></a><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\"> </span>- Location moved to Avatar Editor</span>\n</li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e78c03498c82484b6dc40e423fa31315f\">\n<a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358126161177\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Name Changes (Rename) </span></span></a><span style=\"color: #2F3941;\"> - Check the UI updates page for list</span><br><span style=\"color: #2F3941;\"><span style=\"color: #000000;\"></span></span>\n</li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e5c721a07d51b2ee65263fac1c090bb6d\">\n<a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358263976729\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Object Property Editor (Avatar)</span></span></a><span style=\"color: #2F3941;\"> - Feature name changes &amp; Eye Control added</span><br><span style=\"color: #2F3941;\"><span style=\"color: #000000;\"></span></span>\n</li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e8d391db629eba04f274ea4856b56a866\">\n<a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358200025497\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Garment Rendering</span></span></a><span style=\"color: #2F3941;\">  - New 2D styles added.</span><br><span style=\"color: #2F3941;\"><span style=\"color: #000000;\"></span></span>\n</li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e1c40195a4bd7733ae952bd36472f3eaf\">\n<a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358163705369\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Advanced Pinching</span></span></a><span style=\"color: #2F3941;\"> - More complex Pinching features added.</span>\n</li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"ebff0ef149c23a0d7beb159fe575c8f71\">\n<a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358126161177\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Open/Close Window</span></span></a><span style=\"color: #2F3941;\"> - Open all window types from Main Menu &gt; Window or right-click Title Bar</span>\n</li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e3f684582f9069f72f12bcaa24bbe6d24\">\n<a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358119414937\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">2D Snapshot </span></span></a><span style=\"color: #2F3941;\">- Take a snapshot of the 2D window</span><br><span style=\"color: #2F3941;\"><span style=\"color: #000000;\"></span></span>\n</li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e5aa8e409816d5a3749c73ea3d0be31ca\">\n<a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358354219033\" target=\"_blank\" rel=\"noopener noreferrer\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">3D Snapshot &gt; Multiview Tab</span> </span></a><span style=\"color: #2F3941;\">- You can now take turn around pictures or pictures from multiple angles</span>\n</li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e5ad86d8b319bfaa5900092b219f41e31\">\n<a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358386936473\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">3D Background Setting Aspect Ratio</span></span></a><span style=\"color: #2F3941;\"> - Toggle added to Maintain imported images aspect ratio</span><br><span style=\"color: #2F3941;\"><span style=\"color: #000000;\"></span></span>\n</li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e3c032c61ff30b138603cf32aaf6cc956\">\n<a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358352313881\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Set Editing Range for Curve Lines and Points</span> </span></a><span style=\"color: #2F3941;\">- Curvature of a specific line can be adjusted individually</span>\n</li>\n<li class=\"wysiwyg-list-color\" style=\"--wysiwyg-list-marker-color: #2F3941;\" data-list-item-id=\"e944c78dbb61233ef4f084f3d7270ff01\">\n<a href=\"https://support.marvelousdesigner.com/hc/en-us/articles/47358146261913#h_01JSBHX4A2WCFK9Q0ZGHTHZ182\"><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\">Tack on Trims</span></span></a><span style=\"color: #2F3941;\"><span class=\"wysiwyg-underline\"> </span>-  You can now use Tack to attach Trims to multiple Vertices / across multiple patterns</span>\n</li>\n</ul>"""
    markdown = html_to_markdown_converter(html)
    print(markdown)
