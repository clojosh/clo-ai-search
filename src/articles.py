import json
import multiprocessing
import os
import re
import shutil

import questionary
import requests  # type: ignore
from rich import print

from tools.azure import Azure
from tools.misc import clean_html, extract_structured_markdown, extract_youtube_links, get_section_and_category, num_tokens_from_string, trim_tokens


class Article:
    def __init__(self, azure: Azure):
        self.azure = azure

    def delete_documents(self, article_id: str | list):
        print(f"\nDeleting {article_id}")

        if isinstance(article_id, list):
            for id in article_id:
                self.azure.search_client.upload_documents([{"@search.action": "delete", "ArticleId": id}])
        else:
            self.azure.search_client.upload_documents([{"@search.action": "delete", "ArticleId": article_id}])

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

    @staticmethod
    def extract_content_from_zendesk_article(azure: Azure, brand: str, articles: list):
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
                    url_matches = re.findall(
                        rf"https:\/\/support\.marvelousdesigner\.com\/hc\/{azure.get_locale()}\/articles\/\d+", article["html_url"]
                    )

                if len(url_matches) > 0:
                    article["html_url"] = url_matches[0]

                article["youtube_links"] = extract_youtube_links(str(article["body"]))

                cleaned_soup = clean_html(str(article["body"]))
                article["body"], inline_images = extract_structured_markdown(cleaned_soup)

                article["body"] = trim_tokens(article["body"])

                article["id"] = str(article["id"])
                article["section_id"], article["section"], article["category_id"], article["category"] = get_section_and_category(
                    azure, article["section_id"]
                )

                documents.append(
                    {
                        "ArticleId": article["id"],
                        "Source": article["html_url"],
                        "Title": article["title"],
                        "Content": article["body"],
                        "ContentDescription": azure.openai_helper.create_webpage_description(article["body"]),
                        "CreatedAt": article["updated_at"],
                        "YoutubeLinks": article["youtube_links"],
                        "InlineImages": inline_images,
                        "CategoryId": article["category_id"],
                        "Category": article["category"],
                        "SectionId": article["section_id"],
                        "Section": article["section"],
                        "Tokens": num_tokens_from_string(article["body"], "gpt-4"),
                    }
                )

        return documents

    @staticmethod
    def get_zendesk_documents(stage: str, brand: str, language: str, article_path: str, article_id: str, page: str):
        if article_id is not None:
            print("\nRetrieving Article " + article_id)
        else:
            print("\nRetrieving Page " + str(page))

        azure = Azure(stage, brand, language)

        page_url = requests.request(
            "GET",
            azure.get_zendesk_article_api_endpoint(article_id) if article_id is not None else azure.get_zendesk_articles_api_endpoint(page),
            headers={
                "Content-Type": "application/json",
            },
        )

        json_objects = json.loads(page_url.text)

        documents = Article.extract_content_from_zendesk_article(
            azure, brand, [json_objects["article"]] if article_id is not None else json_objects["articles"]
        )

        if len(documents) > 0:
            with open(os.path.join(article_path, "page_0.json" if article_id is not None else f"page_{page}.json"), "w+", encoding="utf-8") as f:
                json.dump(documents, f, ensure_ascii=False, indent=4)

    def mp_get_zendesk_documents(self):
        headers = {
            "Content-Type": "application/json",
        }

        response = requests.request("GET", self.azure.get_zendesk_articles_api_endpoint(1), headers=headers)
        json_objects = json.loads(response.text)
        page_count = json_objects["page_count"]

        with multiprocessing.Pool(5) as p:
            p.starmap_async(
                Article.get_zendesk_documents,
                [
                    (self.azure.stage, self.azure.brand, self.azure.language, self.azure.get_article_path(), None, page)
                    for page in range(1, 1 + page_count)
                ],
                error_callback=lambda e: print(e),
            )
            p.close()
            p.join()

    @staticmethod
    def upload_documents(stage: str, brand: str, language: str, article_path: str, file: str):
        print(f"\n{file}")

        azure = Azure(stage, brand, language)

        with open(os.path.join(article_path, file), "r", encoding="utf-8") as f:
            documents = json.load(f)

            for i, document in enumerate(documents):
                if document["Content"] == "":
                    document["Content"] = document["Title"]

                documents[i]["@search.action"] = "mergeOrUpload"
                documents[i]["TitleVector"] = azure.openai_helper.generate_embeddings(text=document["Title"])
                documents[i]["ContentVector"] = azure.openai_helper.generate_embeddings(text=document["Content"])

                del documents[i]["Tokens"]
                del documents[i]["SectionId"]
                del documents[i]["Section"]
                del documents[i]["CategoryId"]
                del documents[i]["Category"]

            if brand == "clovf":
                # Upload clovf articles to both clo3d and clo-set
                Azure(stage, "clo3d").search_client.upload_documents(documents)
                Azure(stage, "closet").search_client.upload_documents(documents)
            else:
                azure.search_client.upload_documents(documents)

    def mp_upload_documents(self):
        if self.azure.brand == "allinone":
            file_paths = os.listdir(self.azure.get_article_path())
        else:
            file_paths = sorted(os.listdir(self.azure.get_article_path()), key=lambda x: int(x.partition("_")[2].partition(".")[0]))

        upload_documents_params = []
        for file in file_paths:
            upload_documents_params.append((self.azure.stage, self.azure.brand, self.azure.language, self.azure.get_article_path(), file))

        with multiprocessing.Pool(5) as p:
            p.starmap_async(Article.upload_documents, upload_documents_params, error_callback=lambda e: print(e))
            p.close()
            p.join()


if __name__ == "__main__":
    stage = questionary.select("Which stage?", choices=["dev", "prod"]).ask()
    brand = questionary.select("Which brand?", choices=["clo3d", "closet", "connect", "clovf", "md", "allinone"]).ask()
    language = questionary.select("Which language?", choices=["English", "Korean"]).ask()
    task = questionary.select(
        "What task?",
        choices=[
            "Get Zendesk Article",
            "Get All Zendesk Articles",
            "Upload Article",
            "Upload All Articles",
            "Delete Article",
            "Delete Excluded Articles",
        ],
    ).ask()

    article = Article(Azure(stage, brand, language))

    if task == "Get Zendesk Article":
        article_id = questionary.text("Article ID").ask()
        article.get_zendesk_documents(
            article.azure.stage, article.azure.brand, article.azure.language, article.azure.get_article_path(), article_id, ""
        )

    elif task == "Get All Zendesk Articles":
        article.mp_get_zendesk_documents()

    elif task == "Upload Article":
        article_id = questionary.text("Article ID").ask()

        for page in os.listdir(article.azure.get_article_path()):
            with open(os.path.join(article.azure.get_article_path(), page), "r", encoding="utf-8") as f:
                documents = json.load(f)
                for i, document in enumerate(documents):
                    if document["ArticleId"] == article_id:
                        Article.upload_documents(
                            article.azure.stage, article.azure.brand, article.azure.language, article.azure.get_article_path(), page
                        )
                        break

    elif task == "Upload All Articles":
        # if brand == "allinone":
        #     for folder in os.listdir("data"):
        #         if folder != "allinone":
        #             for file in os.listdir(os.path.join("data", folder, "articles", "en-us")):
        #                 shutil.copy(
        #                     os.path.join("data", folder, "articles", "en-us", file),
        #                     os.path.join(article.azure.get_article_path(), f"{folder}_{file}"),
        #                 )

        article.mp_upload_documents()

    elif task == "Delete Article":
        article_id = questionary.text("Article ID").ask()
        article.delete_documents(article_id)

    elif task == "Delete Excluded Articles":
        article.delete_excluded_documents(brand=brand)
