import os
from pathlib import Path

from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from dotenv import load_dotenv
from openai import AzureOpenAI

from tools.openai_helper import OpenAIHelper

parent_dir_path = Path(__file__).parent.parent.parent
zendesk_article_api_endpoint = "https://{0}.{1}.com/api/v2/help_center/{2}/articles/{3}"
zendesk_articles_api_endpoint = "https://{0}.{1}.com/api/v2/help_center/{2}/articles.json?page={3}&per_page={4}&sort_by=updated_at&sort_order=desc"
zendesk_article_section_api_endpoint = "https://{0}.{1}.com/api/v2/help_center/{2}/sections/{3}.json"
zendesk_article_category_api_endpoint = "https://{0}.{1}.com/api/v2/help_center/{2}/categories/{3}.json"
zendesk_article_attachment_api_endpoint = "https://support.{0}.com/api/v2/help_center/{1}/articles/{2}/attachments"


class Azure:
    def __init__(self, stage="dev", brand="", language="English"):
        self.stage = stage

        if brand == "marvelousdesigner":
            self.brand = "md"
        else:
            self.brand = brand

        self.language = language

        if brand == "cloapi":
            load_dotenv(os.path.join(parent_dir_path, ".env.api.dev"))
        # elif stage == "prod":
        #     load_dotenv(os.path.join(parent_dir_path, ".env.prod"))
        # else:
        #     load_dotenv(os.path.join(parent_dir_path, ".env.dev"))

        self.ZENDESK_USERNAME = os.environ.get("ZENDESK_USERNAME", "")
        self.ZENDESK_PASSWORD = os.environ.get("ZENDESK_PASSWORD", "")

        self.AZURE_SEARCH_SERVICE = os.environ.get("AZURE_SEARCH_SERVICE", "")

        self.INDEX_NAME = os.environ.get(f"{self.brand.upper()}_AZURE_SEARCH_INDEX", "")

        self.SEARCH_CLIENT_ENDPOINT = f"https://{self.AZURE_SEARCH_SERVICE}.search.windows.net"
        self.AZURE_KEY_CREDENTIAL = AzureKeyCredential(os.environ.get("AZURE_SEARCH_KEY", ""))

        self.search_client = SearchClient(
            endpoint=f"https://{self.AZURE_SEARCH_SERVICE}.search.windows.net",
            index_name=self.INDEX_NAME,
            credential=self.AZURE_KEY_CREDENTIAL,
        )

        self.search_index_client = SearchIndexClient(endpoint=f"https://{self.AZURE_SEARCH_SERVICE}.search.windows.net", credential=self.AZURE_KEY_CREDENTIAL)

        self.AZURE_OPENAI_SERVICE = os.environ.get("AZURE_OPENAI_SERVICE", "")
        self.AZURE_OPENAI_CHATGPT_DEPLOYMENT = os.environ.get("AZURE_OPENAI_CHATGPT_DEPLOYMENT", "")
        self.AZURE_OPENAI_EMB_DEPLOYMENT = os.environ.get("AZURE_OPENAI_EMB_DEPLOYMENT", "")
        self.openai_client = AzureOpenAI(
            api_version="2024-08-01-preview",
            azure_endpoint=f"https://{self.AZURE_OPENAI_SERVICE}.openai.azure.com",
            api_key=os.environ.get("AZURE_OPENAI_KEY") or os.environ.get("AZURE_OPENAI_API_KEY", ""),
        )
        self.openai_helper = OpenAIHelper(
            self.openai_client,
            self.AZURE_OPENAI_CHATGPT_DEPLOYMENT,
            self.AZURE_OPENAI_EMB_DEPLOYMENT,
        )

        self.URI = os.environ.get("MONGO_URI", "")
        self.DB_NAME = os.environ.get(f"{self.brand.upper()}_MONGO_DB_NAME", "")
        self.COLLECTION_NAME = os.environ.get("MONGO_COLLECTION_CHATHISTORY", "")
        self.COLLECTION_USERS = os.environ.get("MONGO_COLLECTION_USERS", "")
        self.COLLECTION_ARTICLE = os.environ.get("MONGO_COLLECTION_ARTICLES", "")
        self.COLLECTION_FEEDBACK = os.environ.get("MONGO_COLLECTION_FEEDBACK", "")

    def get_locale(self) -> str:
        """
        Returns the locale for the given language.

        Args:
            self (Azure): The Azure object.

        Returns:
            str: The locale for the given language.
        """

        locale = {
            "English": "en-us",
            "Espanol": "es",
            "Japanese": "ja",
            "Korean": "ko",
            "Portuguese": "pt-br",
            "Chinese": "zh-cn",
            "Taiwanese": "tw",
        }
        return locale[self.language]

    def get_subdomain(self):
        """
        Returns the subdomain based on the brand attribute.

        This method determines the appropriate subdomain for the current brand.

        Returns:
            str: The subdomain corresponding to the brand.
        """

        if self.brand == "closet":
            subdomain = "clo-set-hc"
        elif self.brand == "connect":
            subdomain = "connect-hc"
        elif self.brand == "clovf":
            subdomain = "clovf-hc"
        elif self.brand == "md":
            subdomain = "support"
        else:
            # Default to the brand name if no specific subdomain is found
            subdomain = self.brand

        return subdomain

    def get_domain(self):
        """
        Returns the domain based on the brand attribute.

        This method determines the appropriate domain for the current brand.

        Returns:
            str: The domain corresponding to the brand.
        """

        if self.brand == "md":
            domain = "marvelousdesigner"
        else:
            # Default to the brand name if no specific domain is found
            domain = "zendesk"

        return domain

    def get_article_path(self) -> str:
        """
        Returns the article path for the given language.

        Args:
            self (Azure): The Azure object.

        Returns:
            str: The article path.
        """
        # The article path for different languages
        document_path = {
            "English": os.path.join("data", self.brand, "articles", "en-us"),
            "Espanol": os.path.join("data", self.brand, "articles", "es"),
            "Japanese": os.path.join("data", self.brand, "articles", "ja"),
            "Korean": os.path.join("data", self.brand, "articles", "ko"),
            "Portuguese": os.path.join("data", self.brand, "articles", "pt-br"),
            "Chinese": os.path.join("data", self.brand, "articles", "zh-cn"),
            "Taiwanese": os.path.join("data", self.brand, "articles", "tw"),
        }

        # Create the document path if it doesn't exist
        os.makedirs(document_path[self.language], exist_ok=True)

        return document_path[self.language]

    def get_zendesk_article_api_endpoint(self, article_id: str) -> str:
        """
        Constructs the API endpoint URL for fetching a specific Zendesk article.

        Args:
            page (int): The page number for pagination.

        Returns:
            str: The formatted API endpoint URL.
        """

        return zendesk_article_api_endpoint.format(self.get_subdomain(), self.get_domain(), self.get_locale(), article_id)

    def get_zendesk_articles_api_endpoint(self, page: str, per_page: str = "30") -> str:
        """
        Constructs the API endpoint URL for fetching Zendesk articles.

        Args:
            page (int): The page number for pagination.

        Returns:
            str: The formatted API endpoint URL.
        """

        return zendesk_articles_api_endpoint.format(self.get_subdomain(), self.get_domain(), self.get_locale(), page, per_page)

    def get_zendesk_article_attachment_api_endpoint(self, article_id: str) -> str:
        """
        Constructs the API endpoint URL for fetching Zendesk article attachments.

        Args:
            article_id (int): The ID of the article that has the attachments.

        Returns:
            str: The formatted API endpoint URL.
        """

        return zendesk_article_attachment_api_endpoint.format(self.get_subdomain(), self.get_locale(), article_id)

    def get_zendesk_article_section_api_endpoint(self, section_id):
        """
        Constructs the API endpoint URL for fetching Zendesk article sections.

        Args:
            section_id (int): The ID of the section.

        Returns:
            str: The formatted API endpoint URL.
        """

        return zendesk_article_section_api_endpoint.format(self.get_subdomain(), self.get_domain(), self.get_locale(), section_id)

    def get_zendesk_article_category_api_endpoint(self, category_id):
        """
        Constructs the API endpoint URL for fetching Zendesk article categories.

        Args:
            category_id (int): The ID of the category.

        Returns:
            str: The formatted API endpoint URL.
        """

        return zendesk_article_category_api_endpoint.format(self.get_subdomain(), self.get_domain(), self.get_locale(), category_id)
