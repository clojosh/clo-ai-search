import json
import logging
import os
import re
from pathlib import Path

import html2text
import requests  # type: ignore
import shortuuid
import tiktoken
from bs4 import BeautifulSoup, Tag
from rich import print
from tqdm import tqdm


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
    Converts an HTML string into a Markdown string using the html2text library.

    Args:
        html_content: The input HTML content as a string.

    Returns:
        The converted Markdown content as a string.
    """
    # Create a new converter object
    h = html2text.HTML2Text()

    # Configure the converter for desired Markdown style (optional)
    h.ignore_links = False  # Keep links
    h.ignore_images = False  # Keep images
    h.body_width = 0  # Don't wrap lines
    h.unicode_snob = True  # Use Unicode characters (e.g., proper dashes)

    # Perform the conversion
    markdown_content = h.handle(html_content)

    return markdown_content


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
        inline_images.append({"PlaceHolder": placeholder, "Source": data_image_src, "Alt": alt, "Width": "", "Height": ""})

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


if __name__ == "__main__":
    url = "https://support.clo3d.com/hc/en-us/article_attachments/115000993607/_______.png"

    print(check_image_exists(url))
