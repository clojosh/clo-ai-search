import json
import multiprocessing
import os
import re
from datetime import datetime
from math import ceil

import questionary
import requests  # type: ignore
from rich import print
from tqdm import tqdm

from tools.azure import Azure
from tools.misc import remove_html_tags, trim_tokens

POSTS_ENDPOINT = "https://connect.clo-set.com/api/community/post/search?tags={tags}&category={category}&pageSize={page_size}"
POST_DETAIL = "https://connect.clo-set.com/post/{post_id}"
COMMENTS_ENDPOINT = "https://connect.clo-set.com/api/community/post/{post_id}/comment"


class Posts:
    def __init__(self, azure: Azure):
        self.azure = azure

    @staticmethod
    def generate_posts_endpoint(tags: str = "", category: str = "", search_after: list = [], page_size: str = "30") -> str:
        endpoint = f"https://connect.clo-set.com/api/community/post/search?pageSize={page_size}"

        if tags:
            endpoint += f"&tags={tags}"
        if category:
            endpoint += f"&category={category}"

        if search_after:
            for item in search_after:
                endpoint += f"&searchAfter={item}"

        return endpoint

    @staticmethod
    def get_comments(post_id: str):
        try:
            response = requests.request(
                "GET",
                COMMENTS_ENDPOINT.format(post_id=post_id),
                headers={
                    "Content-Type": "application/json",
                    "x-domain": "https://connect.api.clo-set.com",
                },
            )

            comments = json.loads(response.text)

            combined_comments = ""
            for i, comment in enumerate(comments["comments"]):
                if comment["commentMessage"] is None:
                    continue

                if i != 0:
                    combined_comments += "\n\n"

                combined_comments += f"Comment {i + 1}: " + remove_html_tags(comment["commentMessage"])

                if comment["replies"] is None:
                    continue

                for i, reply in enumerate(comment["replies"]):
                    combined_comments += f"\nReply {i + 1}: " + remove_html_tags(reply["commentMessage"])

            return combined_comments

        except Exception as e:
            print(f"Error fetching comments for post {post_id}: {e}")
            return ""

    @staticmethod
    def get_brand_types_for_post(post: dict):
        brands_found = []

        if "CLO" in post["tags"] or post["title"].lower() in ["clo3d", "clo 3d"] or post["summary"].lower() in ["clo3d", "clo 3d"]:
            brands_found.append("clo3d")

        if "CLO-SET" in post["tags"] or post["title"].lower() in ["closet", "clo-set"] or post["summary"].lower() in ["closet", "clo-set"]:
            brands_found.append("closet")

        if "CONNECT" in post["tags"] or post["title"].lower() in ["connect"] or post["summary"].lower() in ["connect"]:
            brands_found.append("connect")

        if (
            "MarvelousDesigner" in post["tags"]
            or post["title"].lower() in ["marvelous designer", "(md)", " md"]
            or post["summary"].lower() in ["marvelous designer", "(md)", " md"]
        ):
            brands_found.append("md")

        return brands_found

    @staticmethod
    def get_posts(stage: str, brand: str, posts: list, page: int):
        azure = Azure(stage, brand)

        brand_posts: dict = {"clo3d": [], "closet": [], "connect": [], "md": []}
        for post in tqdm(posts, position=((page % 5) + 1), desc=f"Page {page}", colour="red", leave=False):
            # 260 = Job Board
            if post["category"] == 260:
                continue

            brand_types = Posts.get_brand_types_for_post(post)

            comment_content = ""
            if post["commentsCount"] > 0:
                comments = Posts.get_comments(post["postId"])
                if comments:
                    comment_content = "\n\n### Community Post Comments:\n" + comments

            content = trim_tokens(post["summary"]) + comment_content

            document = {
                "id": post["postId"],
                "url": "https://connect.clo-set.com/community/post/" + post["postId"],
                "title": post["title"],
                "content": content,
                "content_description": azure.openai_helper.create_webpage_description(content) if content else "",
                "created_at": post["registeredDate"],
                "category": post["category"],
                "tags": post["tags"],
                "comment_count": post["commentsCount"],
            }

            for brand_type in brand_types:
                brand_posts[brand_type].append(document)

        for brand_type, documents in brand_posts.items():
            if not os.path.exists(os.path.join("data", brand_type, "posts")):
                os.makedirs(os.path.join("data", brand_type, "posts"), exist_ok=True)

            if len(documents) == 0:
                continue

            with open(os.path.join("data", brand_type, "posts", f"page_{page}.json"), "w+", encoding="utf-8") as f:
                json.dump(documents, f, ensure_ascii=False, indent=4)

    def mp_get_posts(self):
        posts_response = requests.request(
            "GET",
            self.generate_posts_endpoint(),
            headers={
                "Content-Type": "application/json",
                "x-domain": "https://connect.api.clo-set.com",
            },
        )

        posts = json.loads(posts_response.text)

        page_count = ceil(posts["totalCount"] / 30)

        tasks = []
        for page in tqdm(range(page_count), desc="Aggregating Posts", colour="green"):
            tasks.append((self.azure.stage, self.azure.brand, posts["posts"], page))

            posts_response = requests.request(
                "GET",
                self.generate_posts_endpoint(search_after=posts["lastSort"]),
                headers={
                    "Content-Type": "application/json",
                    "x-domain": "https://connect.api.clo-set.com",
                },
            )

            posts = json.loads(posts_response.text)

        with multiprocessing.Pool(5) as p:
            p.starmap_async(
                Posts.get_posts,
                tasks,
                error_callback=lambda e: print(e),
            )
            p.close()
            p.join()

    @staticmethod
    def upload(stage: str, brand: str, posts_path: str, file: str, position: int):
        azure = Azure(stage, brand)

        with open(os.path.join(posts_path, file), "r", encoding="utf-8") as f:
            documents = json.load(f)

            try:
                upload_documents = []
                for i, document in enumerate(tqdm(documents, desc=f"Uploading {file}", colour="green", position=position, leave=False)):
                    if document["content"] == "":
                        continue

                    upload_documents.append(
                        {
                            "@search.action": "mergeOrUpload",
                            "ArticleId": document["id"],
                            "Source": document["url"],
                            "Title": document["title"],
                            "Content": document["content"],
                            "ContentDescription": document["content_description"],
                            "CreatedAt": document["created_at"],
                            "YoutubeLinks": [],
                            "titleVector": azure.openai_helper.generate_embeddings(text=document["title"]),
                            "contentVector": azure.openai_helper.generate_embeddings(
                                text=document["content"] if document["content"] != "" else document["post_title"]
                            ),
                        }
                    )

                azure.search_client.upload_documents(upload_documents)

            except Exception:
                print(f"Failed to upload: {document['id']}")

    def mp_upload(self):
        file_paths = sorted(
            os.listdir(os.path.join("data", self.azure.brand, "posts")),
            key=lambda x: int(x.partition("_")[2].partition(".")[0]),
        )

        upload_posts_params = []
        for i, file in enumerate(file_paths):
            upload_posts_params.append((self.azure.stage, self.azure.brand, os.path.join("data", self.azure.brand, "posts"), file, (i % 5) + 1))

        with multiprocessing.Pool(5) as p:
            p.starmap_async(
                Posts.upload,
                upload_posts_params,
                error_callback=lambda e: print(e),
            )
            p.close()
            p.join()

    def delete_posts(self, index_path: str, age: int = 3):
        """
        Delete posts from the AI Search index older than the age(years).

        Args:
            index_path (str): Path to the json file containing the index.
            age (int, optional): The age in years of posts to delete. Defaults to 3.
        """

        with open(index_path, "r", encoding="utf-8") as f:
            documents = json.load(f)

            # Loop through the documents and check the created_at date
            for i, document in enumerate(documents):
                # Get the post from the Zendesk API
                response = requests.request(
                    "GET",
                    f"https://support.clo3d.com/api/v2/community/posts/{document['ArticleId']}",
                    headers={
                        "Content-Type": "application/json",
                    },
                )

                posts_json = json.loads(response.text)
                # print(posts_json)

                # Convert the created_at date to a datetime object
                created_at = datetime.strptime(posts_json["post"]["created_at"], "%Y-%m-%dT%H:%M:%SZ")

                # Set the cutoff date to 3 years ago
                cutoff_date = datetime.strptime(
                    "{}-01-01T00:00:00Z".format(datetime.today().year - age),
                    "%Y-%m-%dT%H:%M:%SZ",
                )

                # If the created_at date is less than the cutoff date, delete the post
                if created_at < cutoff_date:
                    print(f"Deleting {document['ArticleId']}")
                    self.azure.search_client.upload_documents(
                        {
                            "@search.action": "delete",
                            "ArticleId": str(document["ArticleId"]),
                        }
                    )


if __name__ == "__main__":
    stage = questionary.select("Which stage?", choices=["dev", "prod"]).ask()
    task = questionary.select("What task?", choices=["Get Posts", "Upload"]).ask()

    if task == "Get Posts":
        post = Posts(Azure(stage, "clo3d"))
        post.mp_get_posts()

    elif task == "Upload":
        brand = questionary.select("Which brand?", choices=["clo3d", "closet", "connect", "md", "allinone"]).ask()
        post = Posts(Azure(stage, brand))
        post.mp_upload()
