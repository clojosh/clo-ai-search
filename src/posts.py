import json
import multiprocessing
import os
import re
from datetime import datetime

import questionary
import requests  # type: ignore

from tools.azure import Azure
from tools.misc import remove_html_tags, trim_tokens

ZENDESK_POSTS_ENDPOINT = "https://support.{brand}.com/api/v2/help_center/community/posts.json?page={page}&per_page=60"
ZENDESK_COMMENTS_ENDPOINT = "https://support.{brand}.com/api/v2/community/posts/{post_id}/comments?sort_by=created_at&sort_order=asc"


class Posts:
    def __init__(self, azure: Azure):
        self.azure = azure

        if not os.path.exists(os.path.join(azure.brand, "posts")):
            os.makedirs(os.path.join("sources", azure.brand, "posts"), exist_ok=True)

        self.post_dir_path = os.path.join("sources", azure.brand, "posts")

    @staticmethod
    def get_official_comments(brand: str, post_id: str) -> list:
        """
        Prioritizes the official comments for the post. If no official comments exist, it will return the comments.

        Args:
            brand (str): The brand to retrieve the comments for
            post_id (str): The post ID to retrieve the comments for

        Returns:
            list: A list of official comments
        """

        comments_response = requests.request(
            "GET",
            ZENDESK_COMMENTS_ENDPOINT.format(brand=brand, post_id=post_id),
            auth=(
                "share_admin@foxxing.com" if brand == "clo3d" else "joshua.lee@clo3d.com",
                "CLOzendeskshare12#$",
            ),
            headers={
                "Accept": "application/json",
            },
        )

        comment_objects = json.loads(comments_response.text)
        page_count = comment_objects["page_count"]

        comments = []
        for page in range(page_count):
            # Extract the comments for the page
            for comment in comment_objects["comments"]:
                # If the comment is for clo3d, extract the post URL
                if brand == "clo3d":
                    comment["html_url"] = re.findall(
                        r"https:\/\/support\.clo3d\.com\/hc\/en-us\/community\/posts\/\d+",
                        comment["html_url"],
                    )[0]
                # If the comment is for marvelous designer, extract the post URL
                elif brand == "md":
                    comment["html_url"] = re.findall(
                        r"https:\/\/support\.marvelousdesigner\.com\/hc\/en-us\/community\/posts\/\d+",
                        comment["html_url"],
                    )[0]

                comments.append(
                    {
                        "author_id": comment["author_id"],
                        "comment_id": comment["id"],
                        "official": comment["official"],  # true or false
                        "comment_url": comment["html_url"],
                        "comment_body": trim_tokens(remove_html_tags(comment["body"])),
                    }
                )

            # If there is a next page, get the next page of comments
            if comment_objects["next_page"]:
                next_page = requests.request(
                    "GET",
                    comment_objects["next_page"],
                    auth=("share_admin@foxxing.com", "CLOzendeskshare12#$"),
                    headers={
                        "Content-Type": "application/json",
                    },
                )

                comment_objects = json.loads(next_page.text)

        # Filter out the official comments
        official_comments = [comment for comment in comments if comment["official"]]

        # If there are official comments, return them
        if len(official_comments) > 0:
            return official_comments

        # If there are no official comments, return all the comments
        return comments

    @staticmethod
    def get_posts(stage: str, brand: str, page: int, post_dir_path: str) -> list:
        """
        Retrieves posts from Zendesk Community for a given page

        Args:
            brand (str): The brand to retrieve posts for
            page (int): The page number to retrieve posts from
            post_dir_path (str): The directory path to save the posts to

        Returns:
            list: A list of posts with their details and comments
        """
        azure = Azure(stage, brand)

        print(f"Getting posts for page {page}")

        response = requests.request(
            "GET",
            ZENDESK_POSTS_ENDPOINT.format(brand=brand, page=page),
            headers={
                "Content-Type": "application/json",
            },
        )

        posts = json.loads(response.text)

        cutoff_date = datetime.strptime("{}-01-01T00:00:00Z".format(datetime.today().year - 2), "%Y-%m-%dT%H:%M:%SZ")

        filtered_posts = []
        for post in posts["posts"]:
            updated_at = datetime.strptime(post["updated_at"], "%Y-%m-%dT%H:%M:%SZ")

            if brand == "clo3d":
                post_url = re.findall(
                    r"https:\/\/support\.clo3d\.com\/hc\/en-us\/community\/posts\/\d+",
                    post["html_url"],
                )[0]
            elif brand == "marvelousdesigner":
                post_url = re.findall(
                    r"https:\/\/support\.marvelousdesigner\.com\/hc\/en-us\/community\/posts\/\d+",
                    post["html_url"],
                )[0]

            if updated_at >= cutoff_date:
                filtered_posts.append(
                    {
                        "post_id": post["id"],
                        "post_url": post_url,
                        "post_title": post["title"],
                        "post_details": trim_tokens(remove_html_tags(post["details"])),
                        "post_description": azure.openai_helper.create_webpage_description(
                            post["title"] + "\n\n" + trim_tokens(remove_html_tags(post["details"]))
                        ),
                        "created_at": post["created_at"],
                        "comments": Posts.get_official_comments(brand, post["id"]),
                    }
                )

        if len(filtered_posts) > 0:
            with open(
                os.path.join(post_dir_path, f"page_{page}.json"),
                "w+",
                encoding="utf-8",
            ) as f:
                json.dump(filtered_posts, f, ensure_ascii=False, indent=4)

        return filtered_posts

    def mp_get_posts(self):
        brand = self.azure.brand if self.azure.brand != "md" else "marvelousdesigner"

        posts_response = requests.request(
            "GET",
            ZENDESK_POSTS_ENDPOINT.format(brand=brand, page=1),
            headers={
                "Content-Type": "application/json",
            },
        )

        posts_objects = json.loads(posts_response.text)
        page_count = posts_objects["page_count"]

        with multiprocessing.Pool(10) as p:
            p.starmap_async(
                Posts.get_posts,
                [
                    (
                        self.azure.stage,
                        brand,
                        page,
                        self.post_dir_path,
                    )
                    for page in range(1, page_count + 1)
                ],
                error_callback=lambda e: print(e),
            )
            p.close()
            p.join()

    @staticmethod
    def upload(stage: str, posts_path: str, file: str, brand: str):
        print(f"Uploading {file}")

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
    env = questionary.select("Which environment?", choices=["prod", "dev"]).ask()
    brand = questionary.select("Which brand?", choices=["clo3d", "closet", "md"]).ask()
    task = questionary.select("What task?", choices=["Get Posts", "Upload"]).ask()

    post = Posts(Azure(env, brand))

    if task == "Get Posts":
        post.mp_get_posts()

    elif task == "Upload":
        post.mp_upload()
