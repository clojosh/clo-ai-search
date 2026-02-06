import json
import multiprocessing
import os
import re
from datetime import datetime
from math import ceil

import parmap
import questionary
import requests  # type: ignore
from rich import print
from tqdm import tqdm

from ai_search import AISearch
from tools.azure import Azure
from tools.misc import html_to_markdown_converter, remove_html_tags, remove_unwanted_markdown_images, trim_tokens

POSTS_ENDPOINT = "https://connect.clo-set.com/api/community/post/search?tags={tags}&category={category}&pageSize={page_size}"
POST_DETAIL = "https://connect.clo-set.com/api/community/post/{post_id}"
COMMENTS_ENDPOINT = "https://connect.clo-set.com/api/community/post/{post_id}/comment"


class Posts:
    def __init__(self, azure: Azure):
        self.azure = azure

    @staticmethod
    def generate_posts_endpoint(tags: str = "", category: str = "", search_after: list = [], page_size: str = "30") -> str:
        """Generates the endpoint for retrieving a list of posts from the community API.

        Args:
        tags (str): The tags to search for. Defaults to None.
        category (str): The category to search for. Defaults to None.
        search_after (list): The search after parameters. Defaults to [].
        page_size (str): The page size. Defaults to "30".

        Returns:
        str: The endpoint.
        """
        endpoint = f"{POSTS_ENDPOINT.format(tags=tags, category=category, page_size=page_size)}"

        if search_after:
            for item in search_after:
                endpoint += f"&searchAfter={item}"

        return endpoint

    @staticmethod
    def is_comment_allowed(message: str) -> bool:
        """Checks if a comment is allowed.

        Args:
        message (str): The comment to check.

        Returns:
        bool: True if the comment is allowed, False otherwise.
        """
        url_pattern = r"http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+"

        matches = re.findall(url_pattern, message)

        if not matches:
            # If no matches are found, allow the comment
            return True

        for match in matches:
            # Allow comments with links to the following domains
            allowed_domains = ["clo-set", "closet", "marvelousdesigner", "clo3d", "clovf", "connect", "clovirtualfashion"]
            for domain in allowed_domains:
                if domain in match:
                    # If the comment contains a link to one of the allowed domains, allow it
                    return True

        # If the comment doesn't contain any links to the allowed domains, disallow it
        return False

    @staticmethod
    def replace_links_with_community_url(message: str, post_url: str) -> str:
        url_pattern = r"http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+"

        matches = re.findall(url_pattern, message)
        for match in matches:
            message = message.replace(match, post_url)

        return message

    @staticmethod
    def get_comments(post_id: str) -> str:
        """
        Retrieves all the comments for a given post from the community API.

        Args:
        post_id (str): The id of the post to retrieve the comments for.

        Returns:
        str: The combined comments.
        """
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
                # Skip comments that are None or not allowed
                if comment["commentMessage"] is None or not Posts.is_comment_allowed(comment["commentMessage"]):
                    continue

                if i != 0:
                    combined_comments += "\n\n"

                combined_comments += f"Comment {i + 1}: " + Posts.replace_links_with_community_url(
                    trim_tokens(remove_html_tags(comment["commentMessage"])), "https://connect.clo-set.com/community/post/" + post_id
                )

                if comment["replies"] is not None:
                    for i, reply in enumerate(comment["replies"]):
                        combined_comments += f"\nReply {i + 1}: " + Posts.replace_links_with_community_url(
                            trim_tokens(remove_html_tags(reply["commentMessage"])), "https://connect.clo-set.com/community/post/" + post_id
                        )

            return combined_comments

        except Exception as e:
            print(f"Error fetching comments for post {post_id}: {e}")
            return ""

    @staticmethod
    def get_brand_types_for_post(post: dict):
        """
        Determines the brand types associated with a given post based on its tags, title, and summary.

        Args:
        post (dict): A dictionary containing the post's details with keys 'tags', 'title', and 'summary'.

        Returns:
        list: A list of brand types found in the post.
        """
        brands_found = []

        # Check for CLO3D brand
        if "CLO" in post["tags"] or post["title"].lower() in ["clo3d", "clo 3d"] or post["summary"].lower() in ["clo3d", "clo 3d"]:
            brands_found.append("clo3d")

        # Check for CLO-SET brand
        if "CLO-SET" in post["tags"] or post["title"].lower() in ["closet", "clo-set"] or post["summary"].lower() in ["closet", "clo-set"]:
            brands_found.append("closet")

        # Check for CONNECT brand
        if "CONNECT" in post["tags"] or post["title"].lower() in ["connect"] or post["summary"].lower() in ["connect"]:
            brands_found.append("connect")

        # Check for Marvelous Designer brand
        if "MarvelousDesigner" in post["tags"] or post["title"].lower() in ["marvelous designer", "(md)", " md"] or post["summary"].lower() in ["marvelous designer", "(md)", " md"]:
            brands_found.append("md")

        return brands_found

    @staticmethod
    def get_post(stage: str, brand: str, post_id: str):
        azure = Azure(stage, brand)
        response = requests.request(
            "GET",
            POST_DETAIL.format(post_id=post_id),
            headers={
                "Content-Type": "application/json",
                "x-domain": "https://connect.api.clo-set.com",
            },
        )

        post = json.loads(response.text)

        comment_content = ""
        comments = Posts.get_comments(post["postId"])
        if comments:
            comment_content = "\n\n### Community Post Comments:\n" + comments

        markdown = html_to_markdown_converter(post["content"])
        content = remove_unwanted_markdown_images(markdown)

        document = {
            "id": post["postId"],
            "url": "https://connect.clo-set.com/community/post/" + post["postId"],
            "title": post["title"],
            "content": content + comment_content,
            "content_description": azure.openai_helper.create_webpage_description(content) if content else "",
            "created_at": post["registeredDate"],
            "category": post["category"],
            "tags": post["tags"],
        }

        return document

    @staticmethod
    def get_posts(stage: str, brand: str, posts: list, page: int):
        brand_posts: dict = {"clo3d": [], "closet": [], "connect": [], "md": []}
        for post in tqdm(posts, position=((page % 5) + 1), desc=f"Page {page}", colour="red", leave=False):
            # 260 = Job Board
            if post["category"] == 260 or post["category"] == 230:
                continue

            document = Posts.get_post(stage, brand, post["postId"])

            brand_types = Posts.get_brand_types_for_post(post)
            for brand_type in brand_types:
                brand_posts[brand_type].append(document)

        for brand_type, documents in brand_posts.items():
            if not os.path.exists(os.path.join(os.getcwd(), "data", brand_type, "posts")):
                os.makedirs(os.path.join(os.getcwd(), "data", brand_type, "posts"), exist_ok=True)

            if len(documents) == 0:
                continue

            with open(os.path.join(os.getcwd(), "data", brand_type, "posts", f"page_{page}.json"), "w+", encoding="utf-8") as f:
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

    @staticmethod
    def upload(stage: str, brand: str, posts_path: str, file: str, position: int):
        azure = Azure(stage, brand)

        with open(os.path.join(posts_path, file), "r", encoding="utf-8") as f:
            documents = json.load(f)

            try:
                upload_documents = []
                for i, document in enumerate(tqdm(documents, desc=f"Uploading {file}", colour="green", position=position, leave=True)):
                    if document["content"] == "":
                        continue

                    upload_documents.append(
                        {
                            "@search.action": "mergeOrUpload",
                            "article_id": document["id"],
                            "source": document["url"],
                            "title": document["title"],
                            "content": document["content"],
                            "content_description": document["content_description"],
                            "created_at": document["created_at"],
                            "youtube_links": [],
                            "title_vector": azure.openai_helper.generate_embeddings(text=document["title"]),
                            "content_vector": azure.openai_helper.generate_embeddings(text=document["content"] if document["content"] != "" else document["post_title"]),
                        }
                    )

                azure.search_client.upload_documents(upload_documents)

            except Exception:
                print(f"Failed to upload: {document['id']}")

    def mp_upload(self):
        file_paths = sorted(
            os.listdir(os.path.join(os.getcwd(), "data", self.azure.brand, "posts")),
            key=lambda x: int(x.partition("_")[2].partition(".")[0]),
        )

        upload_posts_params = []
        for i, file in enumerate(file_paths):
            upload_posts_params.append((self.azure.stage, self.azure.brand, os.path.join(os.getcwd(), "data", self.azure.brand, "posts"), file, i))

        with multiprocessing.Pool(5) as p:
            p.starmap_async(
                Posts.upload,
                upload_posts_params,
                error_callback=lambda e: print(e),
            )

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
                    f"https://support.clo3d.com/api/v2/community/posts/{document['article_id']}",
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
                    print(f"Deleting {document['article_id']}")
                    self.azure.search_client.upload_documents(
                        {
                            "@search.action": "delete",
                            "article_id": str(document["article_id"]),
                        }
                    )


if __name__ == "__main__":
    stage = questionary.select("Which stage?", choices=["dev", "prod"]).ask()
    brand = questionary.select("Which brand?", choices=["clo3d", "closet", "connect", "md", "allinone"]).ask()
    task = questionary.select("What task?", choices=["Get Post", "Get All Posts", "Upload All Posts", "Find & Delete AI Search Documents"]).ask()

    azure = Azure(stage, brand)
    ai_search = AISearch(Azure(stage, brand))

    if task == "Get Post":
        post_id = questionary.text("Post ID:").ask()

        post = Posts(azure)

        print(post.get_post(stage, "clo3d", post_id=post_id))

    elif task == "Get All Posts":
        post = Posts(azure)
        post.mp_get_posts()

    elif task == "Upload All Posts":
        post = Posts(azure)
        post.mp_upload()

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
