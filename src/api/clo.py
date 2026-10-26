import asyncio
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import questionary
import requests  # type: ignore
import shortuuid
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from tqdm import tqdm

_src_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _src_root not in sys.path:
    sys.path.insert(0, _src_root)

from search.ai_search import AISearch
from tools.azure import Azure
from tools.misc import html_to_markdown_converter


class APICLO:
    def __init__(self, azure: Azure):
        self.azure = azure

        self.base_url = "https://developer.clo3d.com/"

        if not os.path.exists(os.path.join("data", "clo3d", "api")):
            os.makedirs(os.path.join("data", "clo3d", "api"), exist_ok=True)

        self.api_path = os.path.join("data", "clo3d", "api")

    def clean_markdown_content(self, markdown_content: str) -> str:
        regex_pattern_duplicates = r"(!\[.*?\]\((.*?)\))\(\2\)"

        # The replacement string is just Group 1, which is the original image tag.
        replacement = r"\1"

        # Perform the substitution
        cleaned_content = re.sub(regex_pattern_duplicates, replacement, markdown_content)

        img_markdown_regex_pattern = r"(!\[.*?\]\()(.+?\))"

        # The replacement string uses the first capture group (the part before the src: '![alt-text](')
        # then adds the base_url, and finally the second capture group (the src path: '_images/rightarrow.jpeg)')
        replacement = r"\1" + self.base_url + r"\2"

        content = re.sub(img_markdown_regex_pattern, replacement, cleaned_content)

        return content

    def scrape_page_requests(self, page_url: str) -> str:
        # Retrieve the content of the "Environment Setup & Build" page
        response = requests.get(page_url)

        content = html_to_markdown_converter(response.text)

        content = self.clean_markdown_content(content)

        return content

    async def scrape_page_playwright(self, page_url: str) -> str:
        async with async_playwright() as p:
            # Launch browser
            browser = await p.chromium.launch()
            page = await browser.new_page()

            # Navigate to page
            await page.goto(page_url)

            # Wait for dynamic content to load
            await page.wait_for_load_state("networkidle")

            html_content = await page.content()

            # Close browser
            await browser.close()

            return html_content

    def parse_environment_setup_build(self):
        """
        Parse the content of the "Environment Setup & Build" page.
        """

        url = self.base_url + "environment.html"
        content = self.scrape_page_requests(url)

        # Create a dictionary with the article information
        env_setup_build = [
            {
                "article_id": shortuuid.uuid(),  # Generate a unique identifier
                "url": url,  # URL of the original source
                "title": "Environment Setup & Build",  # Title of the article
                "content": content,  # Content of the article
                "content_description": self.azure.openai_helper.create_webpage_description(content),  # Description of the content
                "source": "API",
                "article_created_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),  # Current timestamp
                "article_updated_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),  # Current timestamp
            }
        ]

        # Save the article information as a JSON file
        with open(os.path.join(self.api_path, "env_setup_build.json"), "w+", encoding="utf-8") as f:
            json.dump(env_setup_build, f, indent=4)

    def parse_plugin_management(self):
        """
        Parse the content of the "Plugin Management" page.
        """

        url = self.base_url + "register.html"
        content = self.scrape_page_requests(url)

        # Create a dictionary with the article information
        plugin_management = [
            {
                "article_id": shortuuid.uuid(),  # Generate a unique identifier
                "url": self.base_url + "register.html",  # URL of the original source
                "title": "Plugin Management",  # Title of the article
                "content": content,  # Content of the article
                "content_description": self.azure.openai_helper.create_webpage_description(content),  # Description of the content
                "source": "API",  # Source of the article
                "article_created_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),  # Current timestamp
                "article_updated_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),  # Current timestamp
            }
        ]

        # Save the article information as a JSON file
        with open(os.path.join(self.api_path, "plugin_management.json"), "w+", encoding="utf-8") as f:
            json.dump(plugin_management, f, indent=4)

    async def parse_api_scenario(self):
        url = self.base_url + "scenario.html"
        html_content = await self.scrape_page_playwright(url)

        # Parse with Beautiful Soup
        soup = BeautifulSoup(html_content, "html.parser")

        main_section = soup.find("section", id="api-scenario")

        api_list = []
        for section in main_section.find_all("section"):
            tag_id = section.get("id")
            title = section.find("h2").text

            section_html = str(section)
            section_markdown = html_to_markdown_converter(section_html)
            cleaned_markdown_content = self.clean_markdown_content(section_markdown)

            api_list.append(
                {
                    "article_id": shortuuid.uuid(),
                    "url": url + "#" + tag_id,
                    "source": "API",
                    "title": title.replace("\uf0c1", "").strip(),
                    "content": cleaned_markdown_content,
                    "content_description": "Script for " + title.replace("\uf0c1", "").strip(),
                    "article_created_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "article_updated_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
            )

        with open(os.path.join(self.api_path, "api_scenario.json"), "w+", encoding="utf-8") as f:
            json.dump(
                api_list,
                f,
                indent=4,
            )

    # Uses Playwright to scrape the API documentation files
    async def parse_api_option_type(self):
        url = self.base_url + "optiontype.html"
        html_content = await self.scrape_page_playwright(url)

        # Parse with Beautiful Soup
        soup = BeautifulSoup(html_content, "html.parser")

        main_section = soup.find("section", id="api-option-type")

        api_list = []
        for section in main_section.find_all("section"):
            tag_id = section.get("id")
            title = section.find("h2").text

            section_html = str(section)
            section_markdown = html_to_markdown_converter(section_html)
            cleaned_markdown_content = self.clean_markdown_content(section_markdown)

            api_list.append(
                {
                    "article_id": shortuuid.uuid(),
                    "url": url + "#" + tag_id,
                    "source": "API",
                    "title": title.replace("\uf0c1", "").strip(),
                    "content": cleaned_markdown_content,
                    "content_description": "List of API Option Types",
                    "article_created_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "article_updated_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
            )

        with open(os.path.join(self.api_path, "api_option_type.json"), "w+", encoding="utf-8") as f:
            json.dump(api_list, f, indent=4)

    # Uses Playwright to scrape the API documentation files
    async def parse_api_list(self):
        url = self.base_url + "list.html"
        html_content = await self.scrape_page_playwright(url)

        # Parse with Beautiful Soup
        soup = BeautifulSoup(html_content, "html.parser")

        api_list = []

        for div in soup.find_all("div", class_="highlight-python"):
            tag_id = div.find("pre").get("id")
            title = div.find("pre").find_all("span")[:3]

            pre_html = str(div)
            pre_markdown = html_to_markdown_converter(pre_html)
            cleaned_markdown_content = self.clean_markdown_content(pre_markdown)

            brief_description_index = cleaned_markdown_content.find("@brief")
            param_description_index = cleaned_markdown_content.find("@param")
            return_description_index = cleaned_markdown_content.find("@return")
            if param_description_index != -1:
                content_description = cleaned_markdown_content[brief_description_index:param_description_index]
            elif return_description_index != -1:
                content_description = cleaned_markdown_content[brief_description_index:return_description_index]

            api_list.append(
                {
                    "article_id": shortuuid.uuid(),
                    "url": url + "#" + tag_id,
                    "source": "API",
                    "title": "".join([t.text for t in title]).replace("def", "").replace("\uf0c1", "").strip(),
                    "content": cleaned_markdown_content,
                    "content_description": content_description.replace("@brief ", "").replace("\uf0c1", "").strip(),
                    "article_created_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "article_updated_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
            )

        with open(os.path.join(self.api_path, "api_list.json"), "w+", encoding="utf-8") as f:
            json.dump(
                api_list,
                f,
                indent=4,
            )

    def parse_python_api(self):
        url = self.base_url + "python.html"
        response = requests.get(url)

        markdown_content = html_to_markdown_converter(response.text).replace("[!", "!").replace(")]", ")")

        content = self.clean_markdown_content(markdown_content)

        # Create a dictionary with the article information
        python_api = [
            {
                "article_id": shortuuid.uuid(),  # Generate a unique identifier
                "url": self.base_url + "register.html",  # URL of the original source
                "source": "API",  # Source of the article
                "title": "Python API",  # Title of the article
                "content": content,  # Content of the article
                "content_description": self.azure.openai_helper.create_webpage_description(content),  # Description of the content
                "article_created_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),  # Current timestamp
                "article_updated_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),  # Current timestamp
            }
        ]

        # Save the article information as a JSON file
        with open(os.path.join(self.api_path, "python_api.json"), "w+", encoding="utf-8") as f:
            json.dump(python_api, f, indent=4)

    def parse_library_window_api(self):
        url = self.base_url + "python.html"
        response = requests.get(url)

        markdown_content = html_to_markdown_converter(response.text).replace("[!", "!").replace(")]", ")")

        content = self.clean_markdown_content(markdown_content)

        # Create a dictionary with the article information
        library_window_api = [
            {
                "article_id": shortuuid.uuid(),  # Generate a unique identifier
                "url": url,  # URL of the original source
                "source": "API",  # Source of the article
                "title": "Library Window API",  # Title of the article
                "content": content,  # Content of the article
                "content_description": self.azure.openai_helper.create_webpage_description(content),  # Description of the content
                "article_created_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),  # Current timestamp
                "article_updated_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),  # Current timestamp
            }
        ]

        # Save the article information as a JSON file
        with open(os.path.join(self.api_path, "library_window_api.json"), "w+", encoding="utf-8") as f:
            json.dump(library_window_api, f, indent=4)

    def parse_api_sdk(self):
        url = self.base_url + "download.html"
        response = requests.get(url)

        markdown_content = html_to_markdown_converter(response.text).replace("[!", "!").replace(")]", ")")

        content = self.clean_markdown_content(markdown_content)

        # Create a dictionary with the article information
        api_sdk = [
            {
                "article_id": shortuuid.uuid(),  # Generate a unique identifier
                "url": url,  # URL of the original source
                "source": "API",  # Source of the article
                "title": "SDK",  # Title of the article
                "content": content,  # Content of the article
                "content_description": self.azure.openai_helper.create_webpage_description(content),  # Description of the content
                "article_created_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),  # Current timestamp
                "article_updated_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),  # Current timestamp
            }
        ]

        # Save the article information as a JSON file
        with open(os.path.join(self.api_path, "api_sdk.json"), "w+", encoding="utf-8") as f:
            json.dump(api_sdk, f, indent=4)

    def parse_clo_event_plugin(self):
        url = self.base_url + "eventplugin.html"
        response = requests.get(url)

        markdown_content = html_to_markdown_converter(response.text).replace("[!", "!").replace(")]", ")")

        content = self.clean_markdown_content(markdown_content)

        # Create a dictionary with the article information
        clo_event_plugin = [
            {
                "article_id": shortuuid.uuid(),  # Generate a unique identifier
                "url": url,  # URL of the original source
                "source": "API",  # Source of the article
                "title": "CLO Event Plugin",  # Title of the article
                "content": content,  # Content of the article
                "content_description": self.azure.openai_helper.create_webpage_description(content),  # Description of the content
                "article_created_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),  # Current timestamp
                "article_updated_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),  # Current timestamp
            }
        ]

        # Save the article information as a JSON file
        with open(os.path.join(self.api_path, "clo_event_plugin.json"), "w+", encoding="utf-8") as f:
            json.dump(clo_event_plugin, f, indent=4)

    def parse_plugin_placement_startup(self):
        url = self.base_url + "placement.html"
        response = requests.get(url)

        markdown_content = html_to_markdown_converter(response.text).replace("[!", "!").replace(")]", ")")

        content = self.clean_markdown_content(markdown_content)

        # Create a dictionary with the article information
        plugin_placement_startup = [
            {
                "article_id": shortuuid.uuid(),  # Generate a unique identifier
                "url": url,  # URL of the original source
                "source": "API",  # Source of the article
                "title": "Plugin Placement Startup",  # Title of the article
                "content": content,  # Content of the article
                "content_description": self.azure.openai_helper.create_webpage_description(content),  # Description of the content
                "article_created_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),  # Current timestamp
                "article_updated_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),  # Current timestamp
            }
        ]

        # Save the article information as a JSON file
        with open(os.path.join(self.api_path, "plugin_placement_startup.json"), "w+", encoding="utf-8") as f:
            json.dump(plugin_placement_startup, f, indent=4)

    def upload_document(self, document: dict):
        now = datetime.now(tz=timezone.utc).isoformat()

        # created_at is set once (kept from the existing indexed document if present);
        # updated_at is refreshed on every upload.
        try:
            existing = self.azure.search_client.get_document(key=document["article_id"])
            created_at = existing.get("created_at") or now
        except Exception:
            created_at = now

        document["@search.action"] = "mergeOrUpload"
        document["created_at"] = created_at
        document["updated_at"] = now
        document["title_vector"] = self.azure.openai_helper.generate_embeddings(text=document["title"])
        document["content_vector"] = self.azure.openai_helper.generate_embeddings(text=document["content"])

        self.azure.search_client.upload_documents([document])

    def delete_document(self, api_dir_path: str):
        with open(api_dir_path, "r", encoding="utf-8") as f:
            documents = json.load(f)

            for i, document in enumerate(documents):
                documents[i]["@search.action"] = "delete"

            self.azure.search_client.upload_documents(documents)


if __name__ == "__main__":
    app = questionary.select("What do you want to do?", choices=["Chat Bot", "CLO API"]).ask()
    stage = questionary.select("Which stage?", choices=["dev", "prod"]).ask()
    brand = questionary.select("Which brand?", choices=["clo3d", "closet", "connect", "md", "allinone"]).ask()
    task = questionary.select(
        "What task?",
        choices=[
            "Parse All API Documentation",
            "Parse Environment Setup & Build",
            "Parse Plugin Management",
            "Parse API Scenario",
            "Parse API Option & Type",
            "Parse API List",
            "Parse Python API",
            "Parse API SDK",
            "Parse CLO Event Plugin",
            "Parse Plugin Placement Startup",
            "Upload All Documents",
            "Upload Document",
            "Delete All Documents",
            "Delete Document",
            "Find ",
        ],
    ).ask()

    azure = Azure(app, brand, stage)
    clo_api = APICLO(azure)
    ai_search = AISearch(azure)

    if task == "Parse All API Documentation":
        print("--- Parsing Environment Setup & Build ---")
        clo_api.parse_environment_setup_build()
        print("--- Parsing Plugin Management ---")
        clo_api.parse_plugin_management()
        print("--- Parsing API Scenario ---")
        asyncio.run(clo_api.parse_api_scenario())
        print("--- Parsing API Option & Type ---")
        asyncio.run(clo_api.parse_api_option_type())
        print("--- Parsing API List ---")
        asyncio.run(clo_api.parse_api_list())
        print("--- Parsing Python API ---")
        clo_api.parse_python_api()
        print("--- Parsing Library Window API ---")
        clo_api.parse_library_window_api()
        print("--- Parsing API SDK ---")
        clo_api.parse_api_sdk()
        print("--- Parsing CLO Event Plugin ---")
        clo_api.parse_clo_event_plugin()
        print("--- Parsing Plugin Placement Startup ---")
        clo_api.parse_plugin_placement_startup()

    elif task == "Parse Environment Setup & Build":
        clo_api.parse_environment_setup_build()

    elif task == "Parse Plugin Management":
        clo_api.parse_plugin_management()

    elif task == "Parse API Scenario":
        asyncio.run(clo_api.parse_api_scenario())

    elif task == "Parse API Option & Type":
        asyncio.run(clo_api.parse_api_option_type())

    elif task == "Parse API List":
        asyncio.run(clo_api.parse_api_list())

    elif task == "Parse Python API":
        clo_api.parse_python_api()

    elif task == "Parse Library Window API":
        clo_api.parse_library_window_api()

    elif task == "Parse API SDK":
        clo_api.parse_api_sdk()

    elif task == "Parse CLO Event Plugin":
        clo_api.parse_clo_event_plugin()

    elif task == "Parse Plugin Placement Startup":
        clo_api.parse_plugin_placement_startup()

    elif task == "Upload Document":
        api_document = questionary.select("Which API document?", choices=os.listdir(os.path.join(clo_api.api_path))).ask()
        clo_api.upload_document(os.path.join(clo_api.api_path, api_document))

    elif task == "Upload All Documents":
        file_list = [f for f in os.listdir(clo_api.api_path) if f.endswith(".json")]

        for i, file_name in enumerate(file_list):
            file_path = os.path.join(clo_api.api_path, file_name)

            with open(file_path, "r") as f:
                data = json.load(f)  # Assuming the file contains a list of items

            # Use 10 workers for the items inside THIS file
            with ThreadPoolExecutor(max_workers=10) as executor:
                results = list(tqdm(executor.map(clo_api.upload_document, data), total=len(data), desc=f"Uploading {file_name}", colour="green", position=i + 1, leave=True))

    elif task == "Delete Document":
        api_document = questionary.select("Which API document?", choices=os.listdir(os.path.join(clo_api.api_path))).ask()
        clo_api.delete_document(os.path.join(clo_api.api_path, api_document))

    elif task == "Delete All Documents":
        for files in os.listdir(os.path.join(clo_api.api_path)):
            clo_api.delete_document(os.path.join(clo_api.api_path, files))

    elif task == "Find & Delete AI Search Documents":
        search_fields_options = [
            "article_id",
            "url",
            "title",
            "content",
            "content_description",
            "article_created_at",
            "article_updated_at",
            "created_at",
            "updated_at",
        ]

        search_field = questionary.select("Search field?", choices=search_fields_options).ask()
        search_text = questionary.text("Search value?").ask()

        documents = ai_search.find_all_ai_search_documents(search_fields=[search_field], search_text=search_text)

        for document in documents:
            print(document["article_id"] + "\n" + document["url"], "\n")

        print(f"\nTotal documents found: {len(documents)}\n")

        if questionary.confirm("Do you want to delete these documents?").ask():
            for document in documents:
                ai_search.delete_ai_search_document(document["article_id"])
