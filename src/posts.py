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

        if not os.path.exists(os.path.join(azure.brand, "posts")):
            os.makedirs(os.path.join("data", azure.brand, "posts"), exist_ok=True)

        self.post_dir_path = os.path.join("data", azure.brand, "posts")

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
    def get_posts(stage: str, brand: str, posts: list, page: int, post_dir_path: str, tqdm_position: int) -> list:
        azure = Azure(stage, brand)

        filtered_posts = []
        for post in tqdm(posts, position=tqdm_position, desc=f"Page {page}", colour="red", leave=False):
            comment_content = ""
            if post["commentsCount"] > 0:
                comments = Posts.get_comments(post["postId"])
                if comments:
                    comment_content = "\n\n### Community Post Comments:\n" + comments

            content = trim_tokens(post["summary"]) + comment_content

            filtered_posts.append(
                {
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
            )

        with open(
            os.path.join(post_dir_path, f"page_{page}.json"),
            "w+",
            encoding="utf-8",
        ) as f:
            json.dump(filtered_posts, f, ensure_ascii=False, indent=4)

        return filtered_posts

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
        for i in tqdm(range(page_count), desc="Aggregating Posts", colour="green"):
            tasks.append((self.azure.stage, brand, posts["posts"], i, self.post_dir_path, (i % 5) + 1))

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
    def upload(stage: str, posts_path: str, file: str, brand: str):
        print(f"\nUploading {file}")

        azure = Azure(stage, brand)

        with open(os.path.join(posts_path, file), "r", encoding="utf-8") as f:
            documents = json.load(f)

            upload_documents = []
            for i, document in enumerate(documents):
                content = document["post_details"]
                for comments in document["comments"]:
                    content += " " + comments["comment_body"]

                try:
                    upload_documents.append(
                        {
                            "@search.action": "mergeOrUpload",
                            "ArticleId": str(document["post_id"]),
                            "Source": document["post_url"],
                            "Title": document["post_title"],
                            "Content": content,
                            "ContentDescription": document["post_description"],
                            "CreatedAt": document["created_at"],
                            "YoutubeLinks": [],
                            "titleVector": azure.openai_helper.generate_embeddings(text=document["post_title"]),
                            "contentVector": azure.openai_helper.generate_embeddings(text=content if content != "" else document["post_title"]),
                        }
                    )
                except Exception:
                    print(f"Failed to upload: {document['post_id']}")

            azure.search_client.upload_documents(upload_documents)
            print(f"Uploaded {file}")

    def mp_upload(self):
        file_paths = sorted(
            os.listdir(self.post_dir_path),
            key=lambda x: int(x.partition("_")[2].partition(".")[0]),
        )

        upload_posts_params = []
        for file in file_paths:
            upload_posts_params.append((self.azure.stage, self.post_dir_path, file, self.azure.brand))

        with multiprocessing.Pool(5) as p:
            p.starmap_async(
                Posts.upload,
                upload_posts_params[1:],
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
    brand = questionary.select("Which brand?", choices=["clo3d", "closet", "md"]).ask()
    task = questionary.select("What task?", choices=["Get Posts", "Upload"]).ask()

    post = Posts(Azure(stage, brand))

    if task == "Get Posts":
        post.mp_get_posts()

    elif task == "Upload":
        post.mp_upload()
        post.mp_upload()
