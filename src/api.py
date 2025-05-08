import json
import os
import re
from datetime import datetime, timezone

import questionary
import requests  # type: ignore
import shortuuid

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

        # Create the directory if it doesn't exist
        env_setup_build_path = os.path.join(self.api_path, "env_setup_build")
        if not os.path.exists(env_setup_build_path):
            os.makedirs(env_setup_build_path, exist_ok=True)

        # Save the article information as a JSON file
        with open(os.path.join(env_setup_build_path, "env_setup_build.json"), "w+", encoding="utf-8") as f:
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

        # Create the directory if it doesn't exist
        env_setup_build_path = os.path.join(self.api_path, "api_scenario")
        if not os.path.exists(env_setup_build_path):
            os.makedirs(env_setup_build_path, exist_ok=True)

        # Save the article information as a JSON file
        with open(os.path.join(env_setup_build_path, "api_scenario.json"), "w+", encoding="utf-8") as f:
            json.dump(code_block_dump, f, indent=4)

    def parse_api_list(self):
        """
        Parse the API documentation files and save them as JSON files.
        """
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

            api_list.append(
                {
                    "ArticleId": shortuuid.uuid(),
                    "Source": self.base_url + "list.html",
                    "Title": doc[0].strip(),
                    "Content": doc[1],
                    "ContentDescription": self.azure.openai_helper.create_webpage_description(doc[1]),
                    "CreatedAt": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "YoutubeLinks": [],
                }
            )

        if not os.path.exists(os.path.join(self.api_path, "api_list")):
            os.makedirs(os.path.join(self.api_path, "api_list"), exist_ok=True)

        with open(os.path.join(self.api_path, "api_list", "api_docs.json"), "w+", encoding="utf-8") as f:
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

        # Create the directory if it doesn't exist
        env_setup_build_path = os.path.join(self.api_path, "api_option_type")
        if not os.path.exists(env_setup_build_path):
            os.makedirs(env_setup_build_path, exist_ok=True)

        # Save the article information as a JSON file
        with open(os.path.join(env_setup_build_path, "api_option_type.json"), "w+", encoding="utf-8") as f:
            json.dump(api_option_type, f, indent=4)

    def upload_documents(self):
        for dir in os.listdir(os.path.join(self.api_path)):
            for files in os.listdir(os.path.join(self.api_path, dir)):
                if files.endswith(".json"):
                    print("Uploading: " + os.path.join(self.api_path, dir, files))
                    with open(os.path.join(self.api_path, dir, files), "r", encoding="utf-8") as f:
                        documents = json.load(f)

                        for i, document in enumerate(documents):
                            documents[i]["@search.action"] = "mergeOrUpload"
                            documents[i]["TitleVector"] = self.azure.openai_helper.generate_embeddings(text=document["Title"])
                            documents[i]["ContentVector"] = self.azure.openai_helper.generate_embeddings(text=document["Content"])

                        self.azure.search_client.upload_documents(documents)

    def delete_documents(self):
        for dir in os.listdir(os.path.join(self.api_path)):
            for files in os.listdir(os.path.join(self.api_path, dir)):
                if files.endswith(".json"):
                    with open(os.path.join(self.api_path, dir, files), "r", encoding="utf-8") as f:
                        documents = json.load(f)

                        for i, document in enumerate(documents):
                            documents[i]["@search.action"] = "delete"

                        self.environment.search_client.upload_documents(documents)


if __name__ == "__main__":
    stage = questionary.select("Which stage?", choices=["dev", "prod"]).ask()
    brand = questionary.select("Which brand?", choices=["clo3d", "md"]).ask()
    task = questionary.select(
        "What task?",
        choices=[
            "Parse All API Documentation",
            "Parse API List",
            "Parse Environment Setup & Build",
            "Parse API Scenario",
            "Parse API Option & Type",
            "Upload Documents",
            "Delete Documents",
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

    elif task == "Upload Documents":
        clo_api.upload_documents()

    elif task == "Delete Documents":
        clo_api.delete_documents()
