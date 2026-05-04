import csv
import json
import os
import sys
from pathlib import Path
from typing import List

import questionary
from azure.search.documents.indexes.models import (
    ComplexField,
    ExhaustiveKnnAlgorithmConfiguration,
    ExhaustiveKnnParameters,
    HnswAlgorithmConfiguration,
    HnswParameters,
    SearchableField,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SearchSuggester,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    SimpleField,
    VectorSearch,
    VectorSearchAlgorithmKind,
    VectorSearchAlgorithmMetric,
    VectorSearchProfile,
)
from azure.search.documents.models import VectorizedQuery
from rich import print
from tqdm import tqdm

_src_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _src_root not in sys.path:
    sys.path.insert(0, _src_root)

from tools.azure import Azure

backend_dir = Path(__file__).resolve().parent.parent.parent


class AISearch:
    def __init__(self, azure: Azure):
        self.azure = azure
        self.search_client = azure.search_client
        self.search_index_client = azure.search_index_client
        self.openai_helper = azure.openai_helper

    def text_search(self, text):
        results = self.search_client.search(search_text=text)

        results_list = list(results)

        for i, result in enumerate(results_list):
            print(f"\nTitle:\n{result['Title']}")
            print(f"Source:\n{result['Source']}\n")

    def vector_search(self, query, k=1, print_results=False):
        results = self.search_client.search(
            search_text=None,
            vector=self.openai_helper.generate_embeddings(query),
            vector_fields="TitleVector,ContentVector",
            select=["title", "content", "source"],
            top=k,
        )

        results_list = list(results)

        for i, result in enumerate(results_list):
            results_list[i]["@search.score"] = result["@search.score"] * 100

            if print_results:
                print(f"\nTitle:\n{result['Title']}")
                print(f"Score:\n{result['@search.score']}")
                print(f"Source:\n{result['Source']}\n")

        # print('Results:\n',results_list)

        return results_list

    def hybrid_search(self, query, k=3, print_results=False):
        results = self.search_client.search(
            search_text=query,
            vector=self.openai_helper.generate_embeddings(query),
            vector_fields="TitleVector,ContentVector",
            top=k,
        )

        results_list = list(results)

        for i, result in enumerate(results_list):
            print(f"\nTitle: {result['Title']}")
            print(f"Source:\n{result['Source']}")
            print(f"Labels:\n{result['Labels']}")

        return results_list

    def semantic_vector_search(self, query, k=1, print_results=False):
        results = self.search_client.search(
            search_text=query,
            vector=VectorizedQuery(
                value=self.openai_helper.generate_embeddings(query),
                k=k,
                fields="titleVector,contentVector",
            ),
            select=["title", "content", "source"],
            query_type="semantic",
            query_language="en-us",
            semantic_configuration_name="vector-semantic-config",
            query_caption="extractive",
            query_answer="extractive",
            top=k,
        )

        results_list = list(results)

        # semantic_answers = results.get_answers()
        # for answer in semantic_answers:
        #     if answer.highlights:
        #         print(f"Semantic Answer: {answer.highlights}")
        #     else:
        #         print(f"Semantic Answer: {answer.text}")
        #     print(f"Semantic Answer Score: {answer.score}\n")

        for i, result in enumerate(results_list):
            results_list[i]["@search.score"] = result["@search.score"] * 1000

            if print_results:
                print(f"\nTitle:\n{result['Title']}")
                print(f"Score:\n{result['@search.score']}")
                print(f"Source:\n{result['Source']}\n")

            # captions = result["@search.captions"]
            # if captions:
            #     caption = captions[0]
            #     if caption.highlights:
            #         print(f"Caption:\n{caption.highlights}\n")
            #     else:
            #         print(f"Caption:\n{caption.text}\n")

        # print(results_list)

        return results_list

    def create_search_index(self, index_name=None):
        # Create a search index
        fields = [
            # 1. Identity
            SimpleField(name="article_id", type=SearchFieldDataType.String, key=True),
            SearchableField(name="url", type=SearchFieldDataType.String, filterable=True, searchable=True, retrievable=True, sortable=True),
            # 2. Core Searchable Content
            SearchableField(name="title", type=SearchFieldDataType.String, filterable=True, searchable=True, retrievable=True, sortable=True),
            SearchableField(name="content", type=SearchFieldDataType.String, filterable=True, searchable=True, retrievable=True, sortable=True),
            SearchableField(name="content_description", type=SearchFieldDataType.String, filterable=True, searchable=True, retrievable=True, sortable=True),
            # 3. Metadata
            SearchableField(name="source", type=SearchFieldDataType.String, filterable=True, searchable=True, retrievable=True, sortable=True),
            SearchableField(name="created_at", type=SearchFieldDataType.DateTimeOffset, filterable=True, searchable=True, retrievable=True, sortable=True),
            # 4. Collections
            SearchField(
                name="title_vector",
                type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                searchable=True,
                vector_search_dimensions=1536,
                vector_search_profile_name="HnswProfile",
            ),
            SearchField(
                name="content_vector",
                type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                searchable=True,
                vector_search_dimensions=1536,
                vector_search_profile_name="HnswProfile",
            ),
        ]

        vector_search = VectorSearch(
            algorithms=[
                HnswAlgorithmConfiguration(
                    name="Hnsw",
                    kind=VectorSearchAlgorithmKind.HNSW,
                    parameters=HnswParameters(
                        m=4,
                        ef_construction=400,
                        ef_search=500,
                        metric=VectorSearchAlgorithmMetric.COSINE,
                    ),
                ),
                ExhaustiveKnnAlgorithmConfiguration(
                    name="ExhaustiveKnn",
                    kind=VectorSearchAlgorithmKind.EXHAUSTIVE_KNN,
                    parameters=ExhaustiveKnnParameters(metric=VectorSearchAlgorithmMetric.COSINE),
                ),
            ],
            profiles=[
                VectorSearchProfile(
                    name="HnswProfile",
                    algorithm_configuration_name="Hnsw",
                ),
                VectorSearchProfile(
                    name="ExhaustiveKnnProfile",
                    algorithm_configuration_name="ExhaustiveKnn",
                ),
            ],
        )

        semantic_config = SemanticConfiguration(
            name="semantic-config",
            prioritized_fields=SemanticPrioritizedFields(
                title_field=SemanticField(field_name="title"),
                content_fields=[SemanticField(field_name="content")],
            ),
        )

        # Create the semantic settings with the configuration
        semantic_search = SemanticSearch(configurations=[semantic_config])

        suggester = SearchSuggester(name="TitleContentSG", source_fields=["title", "content"])

        # Create the search index with the semantic settings
        index = SearchIndex(
            name=index_name,
            fields=fields,
            suggesters=[suggester],
            vector_search=vector_search,
            semantic_search=semantic_search,
        )
        result = self.search_index_client.create_or_update_index(index)
        print(f" {result.name} created")

    def drop_search_index(self):
        self.search_index_client.delete_index(self.azure.INDEX_NAME)
        print(f"{self.azure.INDEX_NAME} deleted")

    def find_documents(self, search_fields: list = [], search_text: str = "*", select: list = [], log_results: bool = False):
        results = self.azure.search_client.search(search_fields=search_fields, search_text=search_text, select=select, search_mode="all")

        documents = []
        for result in results:
            document = {}
            for i, field in enumerate(select):
                document[field] = result[field].strip()

                if log_results:
                    if i == 0:
                        print("\n")

                    print(f"[italic red]{field}:[/italic red]\n{result[field]}")

            documents.append(document)

        if log_results:
            print(f"\nDocuments Found: {len(documents)}")

        return documents

    def find_all_ai_search_documents(self, search_fields: List[str] = [], search_text: str = "") -> List[dict]:
        """
        Finds all AI search documents in the Azure Search index

        Args:
            search_fields (List[str], optional): The fields to search in. Defaults to [].
            search_text (str, optional): The text to search for. Defaults to "".

        Returns:
            List[dict]: A list of dictionaries containing the ArticleId and Source of the AI search documents.
        """
        results = self.azure.search_client.search(search_fields=search_fields, search_text=search_text, search_mode="all")

        documents = []
        for r in results:
            documents.append({"article_id": r["article_id"], "url": r["url"]})

        return documents

    def delete_documents(self, results: list):
        upload_documents = []
        for i, result in enumerate(results):
            print(f"Deleting {result['article_id']}")
            upload_documents.append({"@search.action": "delete", "article_id": str(result["article_id"])})

        self.search_client.upload_documents(upload_documents)

    def delete_ai_search_document(self, article_id: int):
        """
        Deletes an AI search document from the Azure Search index

        Args:
            article_id (int): The ID of the AI search document to delete
        """
        document = {"@search.action": "delete", "article_id": article_id}

        print("Deleting " + str(article_id))

        # Upload the document to Azure Search to delete it
        self.azure.search_client.upload_documents([document])

    def get_documents(self, search_fields: list = [], search_text: str = "*", select: list = [], file_type: str = "json", log_results: bool = False):
        results = self.find_documents(search_fields=search_fields, search_text=search_text, select=select, log_results=log_results)

        index_path = os.path.join(backend_dir, "data", self.azure.brand, "indexes", self.azure.stage)

        if not os.path.exists(index_path):
            os.makedirs(index_path, exist_ok=True)

        if file_type == "csv":
            fields = ["article_id", "title", "content", "source"]
            with open(os.path.join(index_path, f"{brand}.csv"), "w", encoding="utf-8") as f:
                write = csv.writer(f)
                write.writerow(fields)

                pbar = tqdm(results, position=1, leave=False, colour="red")
                for i, result in enumerate(pbar):
                    write.writerows([[result["article_id"], result["title"], result["content"], result["source"]]])
        else:
            with open(os.path.join(index_path, f"{brand}.json"), "w+", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=4)


if __name__ == "__main__":
    app = questionary.select("What do you want to do?", choices=["Chat Bot", "CLO API"]).ask()
    stage = questionary.select("Which stage?", choices=["dev", "prod"]).ask()
    brand = questionary.select("Which brand?", choices=["clo3d", "closet", "connect", "md", "allinone"]).ask()
    task = questionary.select(
        "What task?",
        choices=[
            "Create Search Index",
            "Delete Search Index",
            "Get Documents",
            "Search Documents (Hybrid, Text, or Vector)",
            "Find Documents",
            "Delete Documents",
        ],
    ).ask()

    azure = Azure(app, brand, stage)
    ai_search = AISearch(azure)

    if task == "Create Search Index":
        index_name = questionary.text("Index Name?").ask()
        ai_search.create_search_index(index_name)

    elif task == "Delete Search Index":
        ai_search.drop_search_index()

    elif task in ["Delete Documents", "Get Documents", "Find Documents"]:
        search_fields = questionary.checkbox("Search Fields?", choices=["article_id", "title", "source", "content"]).ask()
        search_text = questionary.text("Search Text?").ask()

        if task == "Delete Documents":
            select = ["article_id", "title", "source"]
        else:
            select = questionary.checkbox("Select?", choices=["article_id", "title", "source", "content"]).ask()

        if task == "Delete Documents":
            documents = ai_search.find_documents(search_fields=search_fields, search_text=search_text, select=select)

            for document in documents:
                print(document["article_id"] + "\n" + document["source"], "\n")

            print(f"\nTotal documents found: {len(documents)}\n")

            if questionary.confirm("Do you want to delete these documents?").ask():
                for document in documents:
                    ai_search.delete_documents(documents)

        elif task == "Get Documents":
            ai_search.get_documents(
                search_fields=search_fields,
                search_text=search_text,
                select=select if select != [] else ["article_id", "title", "source"],
                log_results=True,
            )
        elif task == "Find Documents":
            ai_search.find_documents(search_fields=search_fields, search_text=search_text, select=select, log_results=True)

    elif task == "Search Documents":
        search_type = questionary.select("Search Type?", choices=["Hybrid", "Text", "Vector"]).ask()
        search_text = questionary.text("Search Text?", default="*").ask()

        if search_type == "Hybrid":
            ai_search.hybrid_search(search_text)
        elif search_type == "Text":
            ai_search.text_search(search_text)
        elif search_type == "Vector":
            ai_search.vector_search(search_text)
