import json
import logging
import os
import re
from pathlib import Path

import requests  # type: ignore
import shortuuid
import tiktoken
from bs4 import BeautifulSoup
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


def preprocess_html_with_inline_images(html):
    inline_images = []

    soup = BeautifulSoup(html, "html.parser")

    for img in soup.find_all("img"):
        alt = img.get("alt", f"image_{shortuuid.uuid()}.png")
        if alt == "_Divider.png":
            continue

        # Exclude base64 images
        # if img["src"].startswith("data:image/"):
        #     continue

        placeholder = f"[IMAGE:{alt}]"
        inline_images.append({"PlaceHolder": placeholder, "Source": img["src"], "Alt": alt, "Width": img.get("width"), "Height": img.get("height")})
        img.replace_with(placeholder)

    return {"text": soup.get_text(), "inline_images": inline_images}


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


def trim_tokens(article):
    """Removes unnecessary tokens"""
    article = article.replace("\u00a0", " ").replace("&nbsp", " ")
    # article = re.sub(r"[^\w0-9-\s\n_*.`~!@#$%^&()+={}\:\"'?/><,/+\[\]]", "", article)
    article = re.sub(r"\n+", "\n", article)
    article = re.sub(r"\s{2,}", " ", article)

    return article.strip()


def extract_youtube_links(article: str):
    """Return a list of youtube links from an article"""

    iframe_regex = r"<iframe(?:\stitle=\"[a-zA-Z0-9\s]*\")? src=\"[^\s]*\""
    iframes = re.findall(iframe_regex, article)

    youtube_links = []
    for iframe in iframes:
        id = iframe.split("/")[-1].split("?")[0].replace('"', "")
        youtube_links.append("https://www.youtube.com/watch?v={}".format(id))

    return youtube_links


def verify_path(document_path):
    if not os.path.exists("./documents"):
        os.mkdir("./documents")

    if not os.path.exists(document_path):
        os.mkdir(document_path)


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


if __name__ == "__main__":
    text = """<h2 id=\"h_01J7EJ9F1GTRXP8TZVX45J667Z\"><span class=\"wysiwyg-font-size-large\"><strong>New in 2024.2 </strong></span></h2>\n<h4 id=\"h_01J90BBPAEG7D3XYN1PHC8GP1Z\"><span class=\"wysiwyg-font-size-medium wysiwyg-color-black\" style=\"color: #000000;\"><strong>List of Contents</strong></span></h4>\n<p><a href=\"#01J90RTJP3N96C37D2322VAB4K\">Concept</a></p>\n<p><a href=\"#01JARS12G86F1X0Y8927X3CNPQ\">Instruction</a></p>\n<p><a href=\"#01JARS0TGNKP87NQCPAE19WCHE\">FAQ</a><span class=\"wysiwyg-font-size-medium\"></span></p>\n<p> </p>\n<p><img style=\"height: auto;\" src=\"https://support.clo3d.com/hc/article_attachments/39086046335129\" alt=\"_Divider.png\"></p>\n<h4 id=\"01J90RTJP3N96C37D2322VAB4K\" style=\"box-sizing: border-box; font-family: -apple-system, BlinkMacSystemFont, ' Segoe UI' , Helvetica, Arial, sans-serif; text-align: start; font-size: 1.1em; font-weight: 400; margin: 0px 0px 0.67em; color: #40a9ff; font-style: normal; font-variant-ligatures: normal; font-variant-caps: normal; letter-spacing: normal; orphans: 2; text-indent: 0px; text-transform: none; widows: 2; word-spacing: 0px; -webkit-text-stroke-width: 0px; white-space: normal; background-color: #ffffff; text-decoration-thickness: initial; text-decoration-style: initial; text-decoration-color: initial;\"><strong style=\"box-sizing: border-box; font-weight: bold; font-family: -apple-system, BlinkMacSystemFont, ' Segoe UI' , Helvetica, Arial, sans-serif;\">Concept</strong></h4>\n<h4 id=\"h_01JACC2X4QT2ETX54WBE8WRX03\" class=\"element normal\"><span class=\"wysiwyg-color-black\"><strong><span style=\"color: #000000; background-color: #ffffff;\">What is Userpool?</span></strong></span></h4>\n<p class=\"undefined\"><span class=\"wysiwyg-font-size-medium\">A Userpool is a list of Members authorized to use the Owner's license. The license Owner can add or delete Members' email(CLO-SET account) who are allowed to use the license. Once the Member is added to the Userpool, Members can sign in to the software using their own CLO-SET account to use the Owner's license. By doing so, the Owner no longer needs to share the license ID and password, enhancing security and collaboration efficiency.</span><span class=\"wysiwyg-font-size-medium\"></span></p>\n<p class=\"undefined\"> </p>\n<h4 id=\"01JAQ17B9JRBTB40859H4X9FMG\" class=\"element normal\"><span class=\"wysiwyg-color-black\"><strong><span style=\"color: #000000; background-color: #ffffff;\">Who can use Userpool?</span></strong></span></h4>\n<p class=\"undefined\"><span class=\"wysiwyg-font-size-medium\">Any users with the licenses to sign in to CLO Network Onlineauth 2024.2 and later versions.</span></p>\n<p class=\"undefined\"> </p>\n<h4 class=\"element normal\"><span class=\"wysiwyg-color-black\"><strong><span style=\"color: #000000; background-color: #ffffff;\">Where is Userpool provided?</span></strong></span></h4>\n<p><span class=\"wysiwyg-font-size-medium\">The Userpool and a more detailed manual are provided in License Account Admin. Below is how you can access to the Userpool Settings page.</span></p>\n<p><span class=\"wysiwyg-font-size-medium\">Sign in clo3d.com &gt; Click Account( <img src=\"data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACoAAAAiCAYAAAApkEs2AAABYWlDQ1BJQ0MgUHJvZmlsZQAAKJFtkL9Lw1AQx7+plWItVVA6FcmoULXGopNDrSJChVgVf0ymaU2FND6SiLq5ORdEJydB/A+KIKKgm4MoKDgJDroLWbTEe62aVn2P4z58uXt37wv4Qgpjuh9A0bDNzMSouLC4JAZeEUQELfChQ1EtlpTlNJXgOzce5x4Cz3e9/K2LUngn+nx4MJI6K91e9pf+1jecYC5vqZQ/KCSVmTYgxInlDZtx3ibuNGkp4l3OWo2POWdrfFqtmc2kiG+I29WCkiN+Io5l63Stjov6uvq1A98+lDfmZihHKKIYwzjSdEXIkDBMMYBp8uj/nkS1J4U1MGzBxCo0FGBTd5IUBh154kkYUNGHGLGEOEWCe/3bQ08z6L9DLzRq09OWw8DJNY3u8rRumt82BVztM8VUfpwVHL+1MijVuLUMNO+57ts8EOgBKg+u+1523coR0PQInDufNwRjbjU6GIcAAACWZVhJZk1NACoAAAAIAAUBEgADAAAAAQABAAABGgAFAAAAAQAAAEoBGwAFAAAAAQAAAFIBKAADAAAAAQACAACHaQAEAAAAAQAAAFoAAAAAAAAAkAAAAAEAAACQAAAAAQADkoYABwAAABIAAACEoAIABAAAAAEAAAAqoAMABAAAAAEAAAAiAAAAAEFTQ0lJAAAAU2NyZWVuc2hvdJXW38EAAAAJcEhZcwAAFiUAABYlAUlSJPAAAALZaVRYdFhNTDpjb20uYWRvYmUueG1wAAAAAAA8eDp4bXBtZXRhIHhtbG5zOng9ImFkb2JlOm5zOm1ldGEvIiB4OnhtcHRrPSJYTVAgQ29yZSA2LjAuMCI+CiAgIDxyZGY6UkRGIHhtbG5zOnJkZj0iaHR0cDovL3d3dy53My5vcmcvMTk5OS8wMi8yMi1yZGYtc3ludGF4LW5zIyI+CiAgICAgIDxyZGY6RGVzY3JpcHRpb24gcmRmOmFib3V0PSIiCiAgICAgICAgICAgIHhtbG5zOmV4aWY9Imh0dHA6Ly9ucy5hZG9iZS5jb20vZXhpZi8xLjAvIgogICAgICAgICAgICB4bWxuczp0aWZmPSJodHRwOi8vbnMuYWRvYmUuY29tL3RpZmYvMS4wLyI+CiAgICAgICAgIDxleGlmOlVzZXJDb21tZW50PlNjcmVlbnNob3Q8L2V4aWY6VXNlckNvbW1lbnQ+CiAgICAgICAgIDxleGlmOlBpeGVsWERpbWVuc2lvbj40MjwvZXhpZjpQaXhlbFhEaW1lbnNpb24+CiAgICAgICAgIDxleGlmOlBpeGVsWURpbWVuc2lvbj4zNDwvZXhpZjpQaXhlbFlEaW1lbnNpb24+CiAgICAgICAgIDx0aWZmOlJlc29sdXRpb25Vbml0PjI8L3RpZmY6UmVzb2x1dGlvblVuaXQ+CiAgICAgICAgIDx0aWZmOlhSZXNvbHV0aW9uPjE0NC8xPC90aWZmOlhSZXNvbHV0aW9uPgogICAgICAgICA8dGlmZjpZUmVzb2x1dGlvbj4xNDQvMTwvdGlmZjpZUmVzb2x1dGlvbj4KICAgICAgICAgPHRpZmY6T3JpZW50YXRpb24+MTwvdGlmZjpPcmllbnRhdGlvbj4KICAgICAgPC9yZGY6RGVzY3JpcHRpb24+CiAgIDwvcmRmOlJERj4KPC94OnhtcG1ldGE+CpDsbkQAAAFZSURBVFgJ7ZYBDoMgDEVh2b30ZurJ9GZbv0kT3FAKvyQzWRMCOGjfWlqIL5FwA3ncgHFH/IN6R+o2Hn16/vNt2wKayjzPOuR7ZD0r67q+xnFE9fhq0zSx6vf9gdUCyBxg+g1rWKFBzzyZgmINKxSoxZsKzHqVyvo0cQSoq1CgEtKucKnyiLOTfqgdxxhNW0gzgfIoCKX8FEEta4pK2GzE/qvM98h42KCyHgpUUNjFK4fmVexhww1UgdGzpSjVpWM6mYpny2kB/SjRWrosy46kc5SuYRj2by6PE3VtbY/wXiWRELqe16YzmkucT7CzeWuCVYMykArfAlsF6gHZCmsG9YRsgTWXJ+udLhBVIklsWm+6613KywmOWbelLImNQ6nxnFvfAqYz6gmW02W5couhN4dGCHpKEbSncdWt167Oc33xrsed3VssNszlqTdsSf9PhL4Eid9vA/oGWLRG0NeglSwAAAAASUVORK5CYII=\" width=\"19\" height=\"15\"> ) on the top right side &gt; Sign in <img src=\"data:image/png;base64,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\" width=\"97\" height=\"26\"> &gt; Click <img src=\"data:image/png;base64,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\" width=\"130\" height=\"19\"> on the left side</span></p>\n<p> </p>\n<h4 class=\"element normal\"><span class=\"wysiwyg-color-black\"><strong><span style=\"color: #000000; background-color: #ffffff;\"><img style=\"height: auto;\" src=\"https://support.clo3d.com/hc/article_attachments/39086046335129\" alt=\"_Divider.png\"></span></strong></span></h4>\n<h4 id=\"01JARS12G86F1X0Y8927X3CNPQ\" style=\"box-sizing: border-box; font-family: -apple-system, BlinkMacSystemFont, ' Segoe UI' , Helvetica, Arial, sans-serif; text-align: start; font-size: 1.1em; font-weight: 400; margin: 0px 0px 0.67em; color: #40a9ff; font-style: normal; font-variant-ligatures: normal; font-variant-caps: normal; letter-spacing: normal; orphans: 2; text-indent: 0px; text-transform: none; widows: 2; word-spacing: 0px; -webkit-text-stroke-width: 0px; white-space: normal; background-color: #ffffff; text-decoration-thickness: initial; text-decoration-style: initial; text-decoration-color: initial;\"><strong style=\"box-sizing: border-box; font-weight: bold; font-family: -apple-system, BlinkMacSystemFont, ' Segoe UI' , Helvetica, Arial, sans-serif;\">Instruction</strong></h4>\n<h4 class=\"element normal\"><span class=\"wysiwyg-color-black\"><strong><span style=\"color: #000000; background-color: #ffffff;\">How to ADD members to the Userpool</span></strong></span></h4>\n<p><span class=\"wysiwyg-font-size-medium\"><strong><span class=\"wysiwyg-color-black\"><span style=\"color: #000000; background-color: #ffffff;\">From the Userpool Settings</span></span></strong></span></p>\n<p><span class=\"wysiwyg-color-black\"><span style=\"color: #000000; background-color: #ffffff;\"><span class=\"wysiwyg-font-size-medium\">Click </span></span><strong><span style=\"color: #000000; background-color: #ffffff;\"><img src=\"https://support.clo3d.com/hc/article_attachments/39108507889689\" width=\"72\" height=\"25\"></span></strong><span style=\"color: #000000; background-color: #ffffff;\"> <span class=\"wysiwyg-font-size-medium\">and type the Member's email address. For better management, you can set a Member name when adding a member. The Member will receive an invitation email with the software installer included.</span></span></span></p>\n<p> </p>\n<p><span class=\"wysiwyg-color-black\"><strong><span class=\"wysiwyg-font-size-medium\" style=\"color: #000000; background-color: #ffffff;\">From the Software (Auto Add)</span><span style=\"color: #000000; background-color: #ffffff;\"><br></span></strong></span></p>\n<p><span class=\"wysiwyg-color-black\"><span style=\"color: #000000; background-color: #ffffff;\"><span class=\"wysiwyg-font-size-medium\">If the CLO-SET account is signed in from the software, the account will be automatically added as a Member to the Userpool list. The Owner will receive the notification email when the new Member is auto added.</span></span></span></p>\n<p><span class=\"wysiwyg-color-black\"><span style=\"color: #000000; background-color: #ffffff;\"><span class=\"wysiwyg-font-size-medium\">If the Owner doesn't want the Members to be auto added, Auto Add option can be turned off.</span></span></span></p>\n<h4 class=\"element normal\"> </h4>\n<h4 id=\"01JAQ1FVE1MVBNQNJJF4B2W5CM\" class=\"element normal\"><span class=\"wysiwyg-color-black\"><strong><span style=\"color: #000000; background-color: #ffffff;\">How to DELETE members from the Userpool</span></strong></span></h4>\n<p class=\"undefined\"><span class=\"wysiwyg-font-size-medium\"><span style=\"font-size: 10pt; font-family: Arial; font-style: normal;\" data-sheets-root=\"1\">Mark the Members from the list &gt; Click <img src=\"https://support.clo3d.com/hc/article_attachments/39109913714841\" width=\"83\" height=\"24\"></span></span></p>\n<p class=\"undefined\"><span class=\"wysiwyg-font-size-medium\"><span style=\"font-size: 10pt; font-family: Arial; font-style: normal;\" data-sheets-root=\"1\">Deleted Members cannot sign in to the software with their CLO-SET account to use the Owner's license. Deleted Members can be added again whenever it's needed.</span></span><span class=\"wysiwyg-font-size-medium\"><span style=\"font-size: 10pt; font-family: Arial; font-style: normal;\" data-sheets-root=\"1\"></span></span></p>\n<p class=\"undefined\"> </p>\n<h4 class=\"element normal\"><span class=\"wysiwyg-color-black\"><strong><span style=\"color: #000000; background-color: #ffffff;\"><img style=\"height: auto;\" src=\"https://support.clo3d.com/hc/article_attachments/39086046335129\" alt=\"_Divider.png\"></span></strong></span></h4>\n<h4 id=\"01JARS0TGNKP87NQCPAE19WCHE\" style=\"box-sizing: border-box; font-family: -apple-system, BlinkMacSystemFont, ' Segoe UI' , Helvetica, Arial, sans-serif; text-align: start; font-size: 1.1em; font-weight: 400; margin: 0px 0px 0.67em; color: #40a9ff; font-style: normal; font-variant-ligatures: normal; font-variant-caps: normal; letter-spacing: normal; orphans: 2; text-indent: 0px; text-transform: none; widows: 2; word-spacing: 0px; -webkit-text-stroke-width: 0px; white-space: normal; background-color: #ffffff; text-decoration-thickness: initial; text-decoration-style: initial; text-decoration-color: initial;\"><strong style=\"box-sizing: border-box; font-weight: bold; font-family: -apple-system, BlinkMacSystemFont, ' Segoe UI' , Helvetica, Arial, sans-serif;\">FAQ</strong></h4>\n<p><span class=\"wysiwyg-color-black\"><strong><span style=\"color: #000000; background-color: #ffffff;\"><span style=\"font-size: 10pt; font-family: Arial; font-style: normal;\" data-sheets-root=\"1\">Can the license Owner be the Member of the other Userpool? </span><br></span></strong></span></p>\n<p><span class=\"wysiwyg-color-black\"><span style=\"color: #000000; background-color: #ffffff;\"><span style=\"font-size: 10pt; font-family: Arial; font-style: normal;\" data-sheets-root=\"1\">Yes. If the Owner's email has the CLO-SET account and that same account is invited to another Userpool, the account can also be a Member under another Userpool.</span></span></span><span class=\"wysiwyg-color-black\"><span style=\"color: #000000; background-color: #ffffff;\"><span style=\"font-size: 10pt; font-family: Arial; font-style: normal;\" data-sheets-root=\"1\"></span></span></span></p>\n<p> </p>\n<p><strong><span class=\"wysiwyg-color-black\"><span style=\"color: #000000; background-color: #ffffff;\"><span style=\"font-size: 10pt; font-family: Arial; font-style: normal;\" data-sheets-root=\"1\">I am invited as a Userpool Member, but cannot sign in to the software. </span></span></span></strong></p>\n<p><span class=\"wysiwyg-color-black\"><span style=\"color: #000000; background-color: #ffffff;\"><span style=\"font-size: 10pt; font-family: Arial; font-style: normal;\" data-sheets-root=\"1\">The Member needs to be signed up to CLO-SET and have a CLO-SET account to sign in to the software. However, any email can be added when the Owner invites the Members.</span></span></span></p>\n<p> </p>\n<p><strong><span class=\"wysiwyg-color-black\"><span style=\"color: #000000; background-color: #ffffff;\"><span style=\"font-size: 10pt; font-family: Arial; font-style: normal;\" data-sheets-root=\"1\">Do the Userpool Owner and Member related to the CLO-SET group's admin and users(collaborator, editor, viewer)?</span></span></span></strong></p>\n<p><span class=\"wysiwyg-color-black\"><span style=\"color: #000000; background-color: #ffffff;\"><span style=\"font-size: 10pt; font-family: Arial; font-style: normal;\" data-sheets-root=\"1\">No, the Userpool is a separate feature from CLO-SET group permissions. </span></span></span><span class=\"wysiwyg-color-black\"><span style=\"color: #000000; background-color: #ffffff;\"><span style=\"font-size: 10pt; font-family: Arial; font-style: normal;\" data-sheets-root=\"1\"></span></span></span></p>\n<p class=\"undefined\"> </p>"""

    print(preprocess_html_with_inline_images(text))
