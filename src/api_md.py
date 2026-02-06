import asyncio
import json
import multiprocessing
import os
import re
from datetime import datetime, timezone

import questionary
import requests  # type: ignore
import shortuuid
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from tqdm import tqdm

from ai_search import AISearch
from tools.azure import Azure
from tools.misc import html_to_markdown_converter


class APICLO:
    def __init__(self, azure: Azure):
        self.azure = azure

        self.base_url = "https://developer.marvelousdesigner.com/"

        if not os.path.exists(os.path.join("data", "md", "api")):
            os.makedirs(os.path.join("data", "md", "api"), exist_ok=True)

        self.api_path = os.path.join("data", "md", "api")

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
                    "source": url + "#" + tag_id,
                    "title": "".join([t.text for t in title]).replace("def", "").replace("\uf0c1", "").strip(),
                    "content": cleaned_markdown_content,
                    "content_description": content_description.replace("@brief ", "").replace("\uf0c1", "").strip(),
                    "created_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "youtube_links": [],
                }
            )

        with open(os.path.join(self.api_path, "api_list.json"), "w+", encoding="utf-8") as f:
            json.dump(
                api_list,
                f,
                indent=4,
            )

    # Uses Playwright to scrape the API documentation files
    async def parse_api_types(self):
        url = self.base_url + "optiontype.html"
        html_content = await self.scrape_page_playwright(url)

        # Parse with Beautiful Soup
        soup = BeautifulSoup(html_content, "html.parser")

        api_list = []
        section = soup.find("section", id="option-usage-example")

        # The initialization part is not included in any section, so we need to handle it separately
        inialization_section = section.find("div")
        initialization_html = str(inialization_section)
        initialization_markdown = html_to_markdown_converter(initialization_html)
        cleaned_markdown_content = self.clean_markdown_content(initialization_markdown)
        api_list.append(
            {
                "article_id": shortuuid.uuid(),
                "source": url,
                "title": "Initialization of API Option Types",
                "content": cleaned_markdown_content,
                "content_description": "Initialization of API Option Types",
                "created_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "youtube_links": [],
            }
        )

        # Then we can loop through the sections as before
        for section in section.find_all("section"):
            tag_id = section.get("id")
            title = section.find("h4").text

            section_html = str(section)
            section_markdown = html_to_markdown_converter(section_html)
            cleaned_markdown_content = self.clean_markdown_content(section_markdown)

            api_list.append(
                {
                    "article_id": shortuuid.uuid(),
                    "source": url + "#" + tag_id,
                    "title": title.replace("\uf0c1", "").strip(),
                    "content": cleaned_markdown_content,
                    "content_description": "List of API Option Types",
                    "created_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "youtube_links": [],
                }
            )

        with open(os.path.join(self.api_path, "api_option_type.json"), "w+", encoding="utf-8") as f:
            json.dump(api_list, f, indent=4)

    async def parse_python_api(self):
        url = self.base_url + "python.html"

        html_content = await self.scrape_page_playwright(url)

        # Parse with Beautiful Soup
        soup = BeautifulSoup(html_content, "html.parser")
        section = soup.find("section", id="python-api")

        markdown_content = html_to_markdown_converter(str(section))
        content = self.clean_markdown_content(markdown_content)

        # Create a dictionary with the article information
        python_api = [
            {
                "article_id": shortuuid.uuid(),  # Generate a unique identifier
                "source": self.base_url + "register.html",  # URL of the original source
                "title": "Python API",  # Title of the article
                "content": content,  # Content of the article
                "content_description": self.azure.openai_helper.create_webpage_description(content),  # Description of the content
                "created_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),  # Current timestamp
                "youtube_links": [],  # List of YouTube links associated with the article
            }
        ]

        # Save the article information as a JSON file
        with open(os.path.join(self.api_path, "python_api.json"), "w+", encoding="utf-8") as f:
            json.dump(python_api, f, indent=4)

    async def parse_plugin_management(self):
        """
        Parse the content of the "Plugin Management" page.
        """
        url = self.base_url + "register.html"

        html_content = await self.scrape_page_playwright(url)

        # Parse with Beautiful Soup
        soup = BeautifulSoup(html_content, "html.parser")
        section = soup.find("section", id="register-python-plug-in")

        markdown_content = html_to_markdown_converter(str(section))
        content = self.clean_markdown_content(markdown_content)

        # Create a dictionary with the article information
        plugin_management = [
            {
                "article_id": shortuuid.uuid(),  # Generate a unique identifier
                "source": self.base_url + "register.html",  # URL of the original source
                "title": "Plugin Management",  # Title of the article
                "content": content,  # Content of the article
                "content_description": self.azure.openai_helper.create_webpage_description(content),  # Description of the content
                "created_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),  # Current timestamp
                "youtube_links": [],  # List of YouTube links associated with the article
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
                    "source": url + "#" + tag_id,
                    "title": title.replace("\uf0c1", "").strip(),
                    "content": cleaned_markdown_content,
                    "content_description": "Script for " + title.replace("\uf0c1", "").strip(),
                    "created_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "youtube_links": [],
                }
            )

        with open(os.path.join(self.api_path, "api_scenario.json"), "w+", encoding="utf-8") as f:
            json.dump(
                api_list,
                f,
                indent=4,
            )

    @staticmethod
    def upload_document(stage: str, brand: str, api_dir_path: str, file: str, position: int = 0):
        azure = Azure(stage, brand)

        with open(os.path.join(api_dir_path, file), "r", encoding="utf-8") as f:
            documents = json.load(f)

            for i, document in enumerate(tqdm(documents, desc=f"Uploading {os.path.basename(file)}", colour="green", position=position, leave=True)):
                documents[i]["@search.action"] = "mergeOrUpload"
                documents[i]["title_vector"] = azure.openai_helper.generate_embeddings(text=document["title"])
                documents[i]["content_vector"] = azure.openai_helper.generate_embeddings(text=document["content"])

            azure.search_client.upload_documents(documents)

    def mp_upload_documents(self):
        upload_params = []
        for i, file in enumerate(os.listdir(self.api_path)):
            upload_params.append((self.azure.stage, self.azure.brand, self.api_path, file, i))

        with multiprocessing.Pool(10) as p:
            p.starmap_async(
                APICLO.upload_document,
                upload_params,
                error_callback=lambda e: print(e),
            )
            p.close()
            p.join()

    def delete_document(self, api_dir_path: str):
        with open(api_dir_path, "r", encoding="utf-8") as f:
            documents = json.load(f)

            for i, document in enumerate(documents):
                documents[i]["@search.action"] = "delete"

            self.azure.search_client.upload_documents(documents)


if __name__ == "__main__":
    stage = questionary.select("Which stage?", choices=["dev", "prod"]).ask()
    task = questionary.select(
        "What task?",
        choices=[
            "Parse All API Documentation",
            "Parse API List",
            "Parse API Types",
            "Parse Python API",
            "Parse Plugin Management",
            "Parse API Scenario",
            "Upload All Documents",
            "Upload Document",
            "Delete All Documents",
            "Delete Document",
            "Find ",
        ],
    ).ask()

    clo_api = APICLO(Azure(stage, "clo3dapi"))
    ai_search = AISearch(Azure(stage, "clo3d"))

    if task == "Parse All API Documentation":
        print("--- Parsing API List ---")
        asyncio.run(clo_api.parse_api_list())
        print("--- Parsing API Types ---")
        asyncio.run(clo_api.parse_api_types())
        print("--- Parsing Python API ---")
        asyncio.run(clo_api.parse_python_api())
        print("--- Parsing Plugin Management ---")
        asyncio.run(clo_api.parse_plugin_management())
        print("--- Parsing API Scenario ---")
        asyncio.run(clo_api.parse_api_scenario())

    elif task == "Parse API List":
        asyncio.run(clo_api.parse_api_list())

    elif task == "Parse API Types":
        asyncio.run(clo_api.parse_api_types())

    elif task == "Parse Python API":
        print("--- Parsing Python API ---")
        asyncio.run(clo_api.parse_python_api())

    elif task == "Parse Plugin Management":
        asyncio.run(clo_api.parse_plugin_management())

    elif task == "Parse API Scenario":
        asyncio.run(clo_api.parse_api_scenario())

    elif task == "Upload Document":
        api_document = questionary.select("Which API document?", choices=os.listdir(os.path.join(clo_api.api_path))).ask()
        clo_api.upload_document(stage, "clo3dapi", clo_api.api_path, api_document)

    elif task == "Upload All Documents":
        clo_api.mp_upload_documents()

    elif task == "Delete Document":
        api_document = questionary.select("Which API document?", choices=os.listdir(os.path.join(clo_api.api_path))).ask()
        clo_api.delete_document(os.path.join(clo_api.api_path, api_document))

    elif task == "Delete All Documents":
        for files in os.listdir(os.path.join(clo_api.api_path)):
            clo_api.delete_document(os.path.join(clo_api.api_path, files))

    elif task == "Find & Delete AI Search Documents":
        search_fields_options = ["article_id", "source", "title", "content", "content_description"]

        search_field = questionary.select("Search field?", choices=search_fields_options).ask()
        search_text = questionary.text("Search value?").ask()

        documents = ai_search.find_all_ai_search_documents(search_fields=[search_field], search_text=search_text)

        for document in documents:
            print(document["article_id"] + "\n" + document["source"], "\n")

        print(f"\nTotal documents found: {len(documents)}\n")

        if questionary.confirm("Do you want to delete these documents?").ask():
            for document in documents:
                ai_search.delete_ai_search_document(document["article_id"])
