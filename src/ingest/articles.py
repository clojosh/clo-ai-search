import json
import os
import re
import shutil
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import questionary
import requests  # type: ignore
from rich import print
from tqdm import tqdm

_src_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _src_root not in sys.path:
    sys.path.insert(0, _src_root)

from search.ai_search import AISearch
from tools.azure import Azure
from tools.misc import check_image_exists, get_section_and_category, html_to_markdown_converter, num_tokens_from_string, remove_unwanted_markdown_images, trim_tokens


class Article:
    def __init__(self, azure: Azure):
        self.azure = azure

    @staticmethod
    def _raise_if_index_failed(results) -> None:
        """Raise RuntimeError if any Azure Search ``IndexingResult`` has ``succeeded`` false."""
        failed = [r for r in results if not r.succeeded]
        if failed:
            details = "; ".join(f"{r.key}: {r.error_message}" for r in failed)
            raise RuntimeError(f"Azure Search indexing failed ({len(failed)} document(s)): {details}")

    def delete_documents(self, article_id: str | list):
        print(f"\nDeleting {article_id}")

        if isinstance(article_id, list):
            for id in article_id:
                self.azure.search_client.upload_documents([{"@search.action": "delete", "article_id": id}])
        else:
            self.azure.search_client.upload_documents([{"@search.action": "delete", "article_id": article_id}])

    def delete_excluded_documents(self, brand: str):
        headers = {
            "Content-Type": "application/json",
        }

        auth = (self.azure.ZENDESK_USERNAME, self.azure.ZENDESK_PASSWORD)

        response = requests.request("GET", self.azure.get_zendesk_article_api_endpoint("1"), headers=headers, auth=auth)
        json_objects = json.loads(response.text)
        page_count = json_objects["page_count"]

        for page in range(1, 1 + page_count):
            response = requests.request("GET", self.azure.get_zendesk_article_api_endpoint(str(page)), headers=headers, auth=auth)
            json_objects = json.loads(response.text)
            articles = json_objects["articles"]

            for article in articles:
                if (
                    (
                        brand == "closet"
                        and article["section_id"]
                        in [
                            5026352977423,
                            6280973212175,
                            360001149855,
                            360001011655,
                            360000854796,
                            7975498603663,
                        ]
                    )
                    or article["user_segment_id"]
                    or article["draft"]
                ):
                    # print(article["id"])

                    self.delete_documents(str(article["id"]))

    def extract_content_from_zendesk_article(self, brand: str, articles: list):
        documents = []
        for article in articles:
            if article["draft"] is False:
                # CLO3D:
                # 115001436607 - Update Article Section
                # 115012589987 - Requested by John to exclude in CLO3D
                # 360005512874 - References Article Section that has PDF attachments
                # 360002306994 - Lessons Section
                if brand == "clo3d" and (article["section_id"] in [360005512874, 360002306994] or article["id"] in [115012589987]):
                    continue

                # CLOSET Excluded Sections:
                # Joining Connect - Guideline(44750284613145), Uploading(44750300689049), Creating(44750284849433)
                # CLO-SET News - New Feature Updates(44750266902169), CLO-SET News(44750267340697)
                # Account(CVF Articles Cover this section) - 44750313539737
                if brand == "closet" and article["section_id"] in [
                    44750284613145,
                    44750300689049,
                    44750284849433,
                    44750266902169,
                    44750267340697,
                    44750313539737,
                ]:
                    continue

                # Removes title from the URLs
                if brand == "clo3d":
                    url_matches = re.findall(rf"https:\/\/support\.clo3d\.com\/hc\/{azure.get_locale()}\/articles\/\d+", article["html_url"])
                elif brand == "closet":
                    url_matches = re.findall(rf"https:\/\/support\.clo-set\.com\/hc\/{azure.get_locale()}\/articles\/\d+", article["html_url"])
                elif brand == "connect":
                    url_matches = re.findall(rf"https:\/\/connect-hc\.zendesk\.com\/hc\/{azure.get_locale()}\/articles\/\d+", article["html_url"])
                elif brand == "clovf":
                    url_matches = re.findall(rf"https:\/\/clovf-hc\.zendesk\.com\/hc\/{azure.get_locale()}\/articles\/\d+", article["html_url"])
                elif brand == "md":
                    url_matches = re.findall(rf"https:\/\/support\.marvelousdesigner\.com\/hc\/{azure.get_locale()}\/articles\/\d+", article["html_url"])

                if len(url_matches) > 0:
                    article["html_url"] = url_matches[0]

                markdown = html_to_markdown_converter(str(article["body"]))
                markdown = remove_unwanted_markdown_images(markdown)

                article["body"] = trim_tokens(markdown)

                article["id"] = str(article["id"])
                article["section_id"], article["section"], article["category_id"], article["category"] = get_section_and_category(self.azure, article["section_id"])

                documents.append(
                    {
                        "article_id": article["id"],
                        "url": article["html_url"],
                        "title": article["title"],
                        "content": article["body"],
                        "content_description": self.azure.openai_helper.create_webpage_description(article["body"]),
                        "source": "Zendesk",
                        "created_at": article["updated_at"],
                        "category_id": article["category_id"],
                        "category": article["category"],
                        "section_id": article["section_id"],
                        "section": article["section"],
                        "tokens": num_tokens_from_string(article["body"], "gpt-4"),
                    }
                )

        return documents

    @staticmethod
    def find_articles_invalid_images(article: dict):
        invalid_images = defaultdict(set)

        content = html_to_markdown_converter(str(article["body"]))

        IMAGE_PATTERN = re.compile(r"(\!\[.*?\]\((.*?)\))")
        # Find all matches (full string, alt text, URL)
        matches = IMAGE_PATTERN.findall(content)

        for full_match, url_with_title in matches:
            # Split URL from optional title (uses space, then an optional quote)
            url_match = re.match(r'([^"\s]+)', url_with_title.strip())

            if url_match:
                image_url = url_match.group(1).strip()

                if check_image_exists(image_url):
                    print(f"✅ VALID: {image_url}")
                else:
                    print(f"❌ INVALID: {image_url}")
                    invalid_images[article["html_url"]].add(image_url)

        return invalid_images

    def mt_find_articles_invalid_images(self):
        headers = {
            "Content-Type": "application/json",
        }

        # Initial call to get page count
        response = requests.request("GET", self.azure.get_zendesk_articles_api_endpoint(1, 100), headers=headers)
        json_objects = json.loads(response.text)
        page_count = json_objects["page_count"]

        articles = []
        # Note: You could technically thread this loop too if page_count is very high!
        for page in range(1, 1 + page_count):
            response = requests.request("GET", self.azure.get_zendesk_articles_api_endpoint(page, 100), headers=headers)
            json_objects = json.loads(response.text)
            articles.extend(json_objects["articles"])

        # Switching to ThreadPoolExecutor
        all_bad_images = []
        with ThreadPoolExecutor(max_workers=10) as executor:
            # executor.map maintains order, similar to p.map
            results = list(executor.map(Article.find_articles_invalid_images, articles))

        # Process results
        for result in results:
            if result:  # Ensure result isn't None if find_articles_invalid_images fails
                for article_source, image_urls in result.items():
                    all_bad_images.append({"Article URL": article_source, "Bad Image URLs": list(image_urls)})

        # Save to file
        output_path = os.path.join(self.azure.get_article_path(), "articles_with_bad_images.json")
        with open(output_path, "w+", encoding="utf-8") as f:
            json.dump(all_bad_images, f, ensure_ascii=False, indent=4)

    def get_zendesk_articles(self, article_path: str, article_id: str, page: str):
        if article_id:
            print(f"\nRetrieving Page {page}")

        # No need to instantiate Azure again; we use the one attached to this instance
        endpoint = self.azure.get_zendesk_article_api_endpoint(article_id) if article_id is not None else self.azure.get_zendesk_articles_api_endpoint(page)

        try:
            response = requests.get(endpoint, headers={"Content-Type": "application/json"})
            response.raise_for_status()
            json_objects = response.json()

            articles = [json_objects["article"]] if article_id is not None else json_objects["articles"]

            # Use self.azure and self.azure.brand directly
            documents = self.extract_content_from_zendesk_article(self.azure.brand, articles)

            if documents:
                filename = "page_0.json" if article_id is not None else f"page_{page}.json"
                with open(os.path.join(article_path, filename), "w+", encoding="utf-8") as f:
                    json.dump(documents, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"Error on page {page}: {e}")

    def upload_document(self, file_path: str, pos: int = 0):
        """Load article JSON and merge-or-upload each document to Azure AI Search with embeddings.

        Args:
            file_path: Path to a JSON file produced by article extraction (list of document dicts).
            pos: Unused; kept for compatibility with threaded callers that pass an index.

        Side effects:
            Calls Azure OpenAI for embeddings and writes documents via ``SearchClient.upload_documents``.
        """
        with open(file_path, "r", encoding="utf-8") as f:
            documents = json.load(f)

        for i, document in enumerate(documents):
            if document["content"] == "":
                document["content"] = document["title"]

            documents[i]["@search.action"] = "mergeOrUpload"
            documents[i]["title_vector"] = self.azure.openai_helper.generate_embeddings(text=document["title"])
            documents[i]["content_vector"] = self.azure.openai_helper.generate_embeddings(text=document["content"])

            del documents[i]["tokens"]
            del documents[i]["section_id"]
            del documents[i]["section"]
            del documents[i]["category_id"]
            del documents[i]["category"]

        if self.azure.brand == "clovf":
            # Upload clovf articles to both clo3d and clo-set
            self._raise_if_index_failed(Azure(self.azure.stage, "clo3d", self.azure.language).search_client.upload_documents(documents))
            self._raise_if_index_failed(Azure(self.azure.stage, "closet", self.azure.language).search_client.upload_documents(documents))
        else:
            self._raise_if_index_failed(self.azure.search_client.upload_documents(documents))


if __name__ == "__main__":
    stage = questionary.select("Which stage?", choices=["dev", "prod"]).ask()
    brand = questionary.select("Which brand?", choices=["clo3d", "cloapi", "closet", "connect", "clovf", "md", "allinone"]).ask()
    language = questionary.select("Which language?", choices=["English", "Korean"]).ask()
    task = questionary.select(
        "What task?",
        choices=[
            "Get Zendesk Article",
            "Get All Zendesk Articles",
            "Find All Articles with Bad Images",
            "Upload Article",
            "Upload All Articles",
            "Delete Article",
            "Delete Excluded Articles",
            "Find & Delete AI Search Documents",
        ],
    ).ask()

    azure = Azure(stage, brand, language)
    article = Article(azure)
    ai_search = AISearch(azure)

    if task == "Get Zendesk Article":
        article_id = questionary.text("Article ID").ask()
        article.get_zendesk_articles(article.azure.get_article_path(), article_id, "")

    elif task == "Get All Zendesk Articles":
        first_page_resp = requests.get(azure.get_zendesk_articles_api_endpoint(1), headers={"Content-Type": "application/json"})
        page_count = first_page_resp.json().get("page_count", 1)
        article_path = azure.get_article_path()

        with ThreadPoolExecutor(max_workers=10) as executor:
            # 1. Map the futures to a list
            futures = [executor.submit(article.get_zendesk_articles, article_path, None, str(page)) for page in range(1, page_count + 1)]

            # 2. Wrap as_completed with tqdm; call result() so worker exceptions are not swallowed.
            for fut in tqdm(as_completed(futures), total=len(futures), desc="Downloading Articles"):
                fut.result()

    elif task == "Find All Articles with Bad Images":
        article.mt_find_articles_invalid_images()

    elif task == "Upload Article":
        article_id = questionary.text("Article ID").ask()

        for page in os.listdir(article.azure.get_article_path()):
            with open(os.path.join(article.azure.get_article_path(), page), "r", encoding="utf-8") as f:
                documents = json.load(f)
                for i, document in enumerate(documents):
                    if document["article_id"] == article_id:
                        article.upload_document(os.path.join(article.azure.get_article_path(), page))
                        sys.exit()

    elif task == "Upload All Articles":
        if azure.brand == "allinone":
            for folder in os.listdir("data"):
                if folder != "allinone":
                    for file in os.listdir(os.path.join("data", folder, "articles", "en-us")):
                        shutil.copy(
                            os.path.join("data", folder, "articles", "en-us", file),
                            os.path.join(article.azure.get_article_path(), f"{folder}_{file}"),
                        )
            files = os.listdir(azure.get_article_path())
        else:
            files = sorted(os.listdir(azure.get_article_path()), key=lambda x: int(x.partition("_")[2].partition(".")[0]))

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(article.upload_document, os.path.join(azure.get_article_path(), file), i + 1) for i, file in enumerate(files)]

            for fut in tqdm(as_completed(futures), total=len(files), desc="Overall Progress", position=0):
                fut.result()

    elif task == "Delete Article":
        article_id = questionary.text("Article ID").ask()
        article.delete_documents(article_id)

    elif task == "Delete Excluded Articles":
        article.delete_excluded_documents(brand=brand)

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
            for document in documents:
                ai_search.delete_ai_search_document(document["article_id"])
