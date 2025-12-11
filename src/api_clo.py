import asyncio
import json
import os
import re
from datetime import datetime, timezone

import questionary
import requests  # type: ignore
import shortuuid
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from tqdm import tqdm

from tools.azure import Azure


class APICLO:
    def __init__(self, azure: Azure):
        self.azure = azure

        self.base_url = "https://developer.clo3d.com/"

        if not os.path.exists(os.path.join("data", "clo3d", "api")):
            os.makedirs(os.path.join("data", "clo3d", "api"), exist_ok=True)

        self.api_path = os.path.join("data", "clo3d", "api")

    def parse_environment_setup_build(self):
        """
        Parse the content of the "Environment Setup & Build" page and save it as a JSON file.
        """

        # Retrieve the content of the "Environment Setup & Build" page
        url = self.base_url + "_sources/environment.rst.txt"
        response = requests.get(url)

        # Create a dictionary with the article information
        env_setup_build = [
            {
                "article_id": shortuuid.uuid(),  # Generate a unique identifier
                "source": self.base_url + "environment.html",  # URL of the original source
                "title": "Environment Setup & Build",  # Title of the article
                "content": response.text,  # Content of the article
                "content_description": self.azure.openai_helper.create_webpage_description(response.text),  # Description of the content
                "created_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),  # Current timestamp
                "youtube_links": [],  # List of YouTube links associated with the article
            }
        ]

        # Save the article information as a JSON file
        with open(os.path.join(self.api_path, "env_setup_build.json"), "w+", encoding="utf-8") as f:
            json.dump(env_setup_build, f, indent=4)

    async def parse_api_scenario(self):
        async with async_playwright() as p:
            # Launch browser
            browser = await p.chromium.launch()
            page = await browser.new_page()

            # Navigate to page
            await page.goto(self.base_url + "scenario.html")

            # Wait for dynamic content to load
            await page.wait_for_load_state("networkidle")  # Wait for network to be idle

            # Get the fully rendered HTML
            html_content = await page.content()

            # Close browser
            await browser.close()

            # Parse with Beautiful Soup
            soup = BeautifulSoup(html_content, "html.parser")

            # print(soup.prettify())

            api_list = []

            main_section = soup.find("section", id="api-scenario")

            for section in main_section.find_all("section"):
                tag_id = section.get("id")
                title = section.find("h2").text
                code_block = section.find("pre").text

                api_list.append(
                    {
                        "article_id": shortuuid.uuid(),
                        "source": self.base_url + "scenario.html" + "#" + tag_id,
                        "title": title.replace("\uf0c1", "").strip(),
                        "content": code_block.strip(),
                        "content_description": "Script for " + title.replace("\uf0c1", "").strip(),
                        "created_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "youtube_links": [],
                    }
                )

            with open(os.path.join(self.api_path, "api_scenario.json"), "w+", encoding="utf-8") as f:
                json.dump(api_list, f, indent=4)

    # Uses Playwright to scrape the API documentation files
    async def parse_api_list(self):
        async with async_playwright() as p:
            # Launch browser
            browser = await p.chromium.launch()
            page = await browser.new_page()

            # Navigate to page
            await page.goto(self.base_url + "list.html")

            # Wait for dynamic content to load
            await page.wait_for_load_state("networkidle")  # Wait for network to be idle

            # Or wait for specific element
            # page.wait_for_selector('#myDiv', timeout=5000)

            # Get the fully rendered HTML
            html_content = await page.content()

            # Close browser
            await browser.close()

            # Parse with Beautiful Soup
            soup = BeautifulSoup(html_content, "html.parser")

            # print(soup.prettify())

            api_list = []

            for div in soup.find_all("div", class_="highlight-python"):
                tag_id = div.find("pre").get("id")
                code_block = div.text.split("\n\n")[0]
                title = code_block.split("\n")[0].split("->")[0].replace("def", "")

                brief = re.search(r"@brief.*", code_block)
                if brief:
                    content_description = brief.group(0).replace("@brief ", "").strip()
                else:
                    content_description = ""

                api_list.append(
                    {
                        "article_id": shortuuid.uuid(),
                        "source": self.base_url + "list.html" + "#" + tag_id,
                        "title": title.replace("\uf0c1", "").strip(),
                        "content": code_block.strip(),
                        "content_description": content_description.replace("\uf0c1", "").strip(),
                        "created_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "youtube_links": [],
                    }
                )

            with open(os.path.join(self.api_path, "api_list.json"), "w+", encoding="utf-8") as f:
                json.dump(api_list, f, indent=4)

    # Uses Playwright to scrape the API documentation files
    async def parse_api_option_type(self):
        async with async_playwright() as p:
            # Launch browser
            browser = await p.chromium.launch()
            page = await browser.new_page()

            # Navigate to page
            await page.goto(self.base_url + "optiontype.html")

            # Wait for dynamic content to load
            await page.wait_for_load_state("networkidle")  # Wait for network to be idle

            # Get the fully rendered HTML
            html_content = await page.content()

            # Close browser
            await browser.close()

            # Parse with Beautiful Soup
            soup = BeautifulSoup(html_content, "html.parser")

            # print(soup.prettify())

            api_list = []

            main_section = soup.find("section", id="api-option-type")

            for section in main_section.find_all("section"):
                tag_id = section.get("id")
                title = section.find("h2").text
                code_block = section.find("pre").text

                api_list.append(
                    {
                        "article_id": shortuuid.uuid(),
                        "source": self.base_url + "optiontype.html" + "#" + tag_id,
                        "title": title.replace("\uf0c1", "").strip(),
                        "content": code_block.strip(),
                        "content_description": "List of API Option Types",
                        "created_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "youtube_links": [],
                    }
                )

            with open(os.path.join(self.api_path, "api_option_type.json"), "w+", encoding="utf-8") as f:
                json.dump(api_list, f, indent=4)

    def upload_document(self, api_dir_path: str):
        with open(api_dir_path, "r", encoding="utf-8") as f:
            documents = json.load(f)

            for i, document in enumerate(tqdm(documents, desc=f"Uploading {os.path.basename(api_dir_path)}", colour="green")):
                documents[i]["@search.action"] = "mergeOrUpload"
                documents[i]["title_vector"] = self.azure.openai_helper.generate_embeddings(text=document["title"])
                documents[i]["content_vector"] = self.azure.openai_helper.generate_embeddings(text=document["content"])

            self.azure.search_client.upload_documents(documents)

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
            "Upload All Documents",
            "Delete All Documents",
            "Parse API List",
            "Parse Environment Setup & Build",
            "Parse API Scenario",
            "Parse API Option & Type",
            "Upload Document",
            "Delete Document",
        ],
    ).ask()

    clo_api = APICLO(Azure(stage, "clo3dapi"))

    if task == "Parse All API Documentation":
        print("--- Parsing Environment Setup & Build ---")
        clo_api.parse_environment_setup_build()
        print("--- Parsing API Scenario ---")
        asyncio.run(clo_api.parse_api_scenario())
        print("--- Parsing API List ---")
        asyncio.run(clo_api.parse_api_list())
        print("--- Parsing API Option & Type ---")
        asyncio.run(clo_api.parse_api_option_type())

    elif task == "Parse API List":
        asyncio.run(clo_api.parse_api_list())

    elif task == "Parse Environment Setup & Build":
        clo_api.parse_environment_setup_build()

    elif task == "Parse API Scenario":
        asyncio.run(clo_api.parse_api_scenario())

    elif task == "Parse API Option & Type":
        asyncio.run(clo_api.parse_api_option_type())

    elif task == "Upload Document":
        api_document = questionary.select("Which API document?", choices=os.listdir(os.path.join(clo_api.api_path))).ask()
        clo_api.upload_document(os.path.join(clo_api.api_path, api_document))

    elif task == "Upload All Documents":
        for files in os.listdir(os.path.join(clo_api.api_path)):
            clo_api.upload_document(os.path.join(clo_api.api_path, files))

    elif task == "Delete Document":
        api_document = questionary.select("Which API document?", choices=os.listdir(os.path.join(clo_api.api_path))).ask()
        clo_api.delete_document(os.path.join(clo_api.api_path, api_document))

    elif task == "Delete All Documents":
        for files in os.listdir(os.path.join(clo_api.api_path)):
            clo_api.delete_document(os.path.join(clo_api.api_path, files))
