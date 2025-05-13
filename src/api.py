import json
import os
import re
from datetime import datetime, timezone

import questionary
import requests  # type: ignore
import shortuuid
from tqdm import tqdm

from tools.azure import Azure
from tools.misc import trim_tokens


class API:
    def __init__(self, azure: Azure):
        self.azure = azure

        if not os.path.exists(os.path.join("data", azure.brand, "api")):
            os.makedirs(os.path.join("data", azure.brand, "api"), exist_ok=True)

        self.api_path = os.path.join("data", azure.brand, "api")

        if azure.brand == "md":
            self.base_url = "https://developer.marvelousdesigner.com/"
        else:
            self.base_url = f"https://developer.{azure.brand}.com/"

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
                "ArticleId": shortuuid.uuid(),  # Generate a unique identifier
                "Source": self.base_url + "environment.html",  # URL of the original source
                "Title": "Environment Setup & Build",  # Title of the article
                "Content": response.text,  # Content of the article
                "ContentDescription": self.azure.openai_helper.create_webpage_description(response.text),  # Description of the content
                "CreatedAt": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),  # Current timestamp
                "YoutubeLinks": [],  # List of YouTube links associated with the article
            }
        ]

        # Save the article information as a JSON file
        with open(os.path.join(self.api_path, "env_setup_build.json"), "w+", encoding="utf-8") as f:
            json.dump(env_setup_build, f, indent=4)

    def parse_api_scenario(self):
        """
        Parse the content of the "API Scenario" page and save it as a JSON file.
        """

        # Retrieve the content of the "Environment Setup & Build" page
        url = self.base_url + "_sources/scenario.rst.txt"
        response = requests.get(url)

        code_block = response.text.split("\n|\n|\n|\n\n\n")

        code_block_dump = []
        for code in code_block:
            title = (
                code.split("code-block")[0]
                .replace("API Scenario", "")
                .replace("\n", "")
                .replace(".", "")
                .replace("-", "")
                .replace("=", "")
                .replace("*", "")
                .strip()
                + " Python Script"
            )

            code_block_dump.append(
                {
                    "ArticleId": shortuuid.uuid(),  # Generate a unique identifier
                    "Source": self.base_url + "scenario.html",  # URL of the original source
                    "Title": title,  # Title of the article
                    "Content": code.replace("API Scenario", "").replace("=======================", "").replace("****", ""),  # Content of the article
                    "ContentDescription": self.azure.openai_helper.create_webpage_description(code),  # Description of the content
                    "CreatedAt": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),  # Current timestamp
                    "YoutubeLinks": [],  # List of YouTube links associated with the article
                }
            )

        # Save the article information as a JSON file
        with open(os.path.join(self.api_path, "api_scenario.json"), "w+", encoding="utf-8") as f:
            json.dump(code_block_dump, f, indent=4)

    def parse_api_list(self):
        """
        Parse the API documentation files and save them as JSON files.
        """

        def extract_python_code_blocks(rst_text: str):
            """Extract Python code blocks from a reStructuredText string."""
            code_blocks = []
            pattern = r"\.\. code-tab:: python\n\n((?:\t{2,}.*\n?)+)"
            matches = re.findall(pattern, rst_text)
            for match in matches:
                # Remove leading indentation
                lines = [line.lstrip() for line in match.strip().splitlines()]
                code_blocks.append("\n".join(lines))
            return "\n\n".join(code_blocks)

        url = self.base_url + "_sources/list.rst.txt"
        response = requests.get(url)

        if response.status_code != 200:
            print(f"Error: {response.status_code}")
            return

        api_text = response.text

        api_list = []
        matches = [(m.start(0), m.end(0)) for m in re.finditer(r".*_API\n", api_text)]
        for i in range(len(matches)):
            if i + 1 == len(matches):
                doc = api_text[matches[i][0] :].split("********")
            else:
                doc = api_text[matches[i][0] : matches[i + 1][0]].split("***********")

            doc[1] = extract_python_code_blocks(doc[1])

            title = doc[0].strip()
            if title == "EXPORT_API":
                source = self.base_url + "list.html#export-api"
            elif title == "IMPORT_API":
                source = self.base_url + "list.html#import-api"
            elif title == "UTILITY_API":
                source = self.base_url + "list.html#utility-api"
            elif title == "FABRIC_API":
                source = self.base_url + "list.html#fabric-api"
            elif title == "PATTERN_API":
                source = self.base_url + "list.html#pattern-api"
            elif title == "REST_API":
                source = self.base_url + "list.html#rest-api"
            else:
                source = self.base_url + "list.html"

            api_list.append(
                {
                    "ArticleId": shortuuid.uuid(),
                    "Source": source,
                    "Title": title,
                    "Content": doc[1],
                    "ContentDescription": self.azure.openai_helper.create_webpage_description(doc[1]),
                    "CreatedAt": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "YoutubeLinks": [],
                }
            )

        with open(os.path.join(self.api_path, "api_list.json"), "w+", encoding="utf-8") as f:
            json.dump(api_list, f, indent=4)

    def parse_api_option_type(self):
        """
        Parse the content of the "API Option & Type" page and save it as a JSON file.
        """

        # Retrieve the content of the "Environment Setup & Build" page
        url = self.base_url + "_sources/optiontype.rst.txt"
        response = requests.get(url)

        api_option_type = []
        matches = [(m.start(0), m.end(0)) for m in re.finditer(r".*\n-{4,}", response.text)]
        for i in range(len(matches)):
            if i + 1 == len(matches):
                doc = response.text[matches[i][0] :].split("----")
            else:
                doc = response.text[matches[i][0] : matches[i + 1][0]].split("----")

            doc = list(filter(lambda x: x.strip() != "", doc))

            api_option_type.append(
                {
                    "ArticleId": shortuuid.uuid(),  # Generate a unique identifier
                    "Source": self.base_url + "optiontype.html",  # URL of the original source
                    "Title": doc[0].strip(),  # Title of the article
                    "Content": doc[1].strip(),  # Content of the article
                    "ContentDescription": self.azure.openai_helper.create_webpage_description(doc[1].strip()),  # Description of the content
                    "CreatedAt": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),  # Current timestamp
                    "YoutubeLinks": [],  # List of YouTube links associated with the article
                }
            )

        # Save the article information as a JSON file
        with open(os.path.join(self.api_path, "api_option_type.json"), "w+", encoding="utf-8") as f:
            json.dump(api_option_type, f, indent=4)

    def upload_document(self, api_dir_path: str):
        with open(api_dir_path, "r", encoding="utf-8") as f:
            documents = json.load(f)

            for i, document in enumerate(tqdm(documents, desc="Uploading documents", colour="green")):
                documents[i]["@search.action"] = "mergeOrUpload"
                documents[i]["TitleVector"] = self.azure.openai_helper.generate_embeddings(text=document["Title"])
                documents[i]["ContentVector"] = self.azure.openai_helper.generate_embeddings(text=document["Content"])

            self.azure.search_client.upload_documents(documents)

    def delete_document(self, api_dir_path: str):
        with open(api_dir_path, "r", encoding="utf-8") as f:
            documents = json.load(f)

            for i, document in enumerate(documents):
                documents[i]["@search.action"] = "delete"

            self.azure.search_client.upload_documents(documents)


if __name__ == "__main__":
    stage = questionary.select("Which stage?", choices=["dev", "prod"]).ask()
    brand = questionary.select("Which brand?", choices=["clo3d", "md"]).ask()
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

    clo_api = API(Azure(stage, brand))

    if task == "Parse All API Documentation":
        clo_api.parse_api_list()
        clo_api.parse_environment_setup_build()
        clo_api.parse_api_scenario()
        clo_api.parse_api_option_type()

    elif task == "Parse API List":
        clo_api.parse_api_list()

    elif task == "Parse Environment Setup & Build":
        clo_api.parse_environment_setup_build()

    elif task == "Parse API Scenario":
        clo_api.parse_api_scenario()

    elif task == "Parse API Option & Type":
        clo_api.parse_api_option_type()

    elif task == "Upload Document":
        api_document = questionary.select("Which API document?", choices=os.listdir(os.path.join(clo_api.api_path))).ask()
        clo_api.upload_document(os.path.join(clo_api.api_path, api_document))

    elif task == "Upload All Documents":
        for dir in os.listdir(os.path.join(clo_api.api_path)):
            for files in os.listdir(os.path.join(clo_api.api_path, dir)):
                clo_api.upload_document(os.path.join(clo_api.api_path, dir, files))

    elif task == "Delete Document":
        api_document = questionary.select("Which API document?", choices=os.listdir(os.path.join(clo_api.api_path))).ask()
        clo_api.delete_document(os.path.join(clo_api.api_path, api_document))

    elif task == "Delete All Documents":
        for dir in os.listdir(os.path.join(clo_api.api_path)):
            for files in os.listdir(os.path.join(clo_api.api_path, dir)):
                clo_api.delete_document(os.path.join(clo_api.api_path, dir, files))
