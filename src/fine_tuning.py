import json
import os

from tools.azure_env import AzureEnv
from tools.openai_helper import OpenAIHelper


class FineTuning:
    def __init__(self, azure_env: AzureEnv):
        self.azure_env = azure_env
        self.openai_helper = OpenAIHelper(azure_env.openai_client, azure_env.AZURE_OPENAI_CHATGPT_DEPLOYMENT, azure_env.AZURE_OPENAI_EMB_DEPLOYMENT)

    def get_training_set(self, documents: list):
        training_set = []
        for document in documents:
            messages = {
                "messages": [
                    {
                        "role": "system",
                        "content": """Act as a tech support representative for CLO/CLO3D software.""",
                    },
                ]
            }

            question = document["Title"]
            if "?" not in document["Title"]:
                question = self.openai_helper.generate_questions(document["Title"])

            messages["messages"].append({"role": "user", "content": question})
            messages["messages"].append({"role": "assistant", "content": document["Content"] + " [" + document["Source"] + "]"})

            training_set.append(messages)

        return training_set

    def fine_tune(self):
        training_set = []
        for json_file in os.listdir(self.azure_env.get_article_path()):
            if not json_file.endswith(".json"):
                continue

            print(f"Creating training and validation set from {json_file}")

            with open(os.path.join(self.azure_env.get_article_path(), json_file), "r", encoding="utf-8") as f:
                documents = json.load(f)
                training_set.extend(self.get_training_set(documents))

        validation_set = training_set[int(len(training_set) * -0.2) :]
        training_set = training_set[: int(len(training_set) * 0.8)]

        with open(os.path.join(self.azure_env.get_article_path(), "training_set.jsonl"), "w+") as f:
            for t in training_set:
                f.write(json.dumps(t) + "\n")

        with open(os.path.join(self.azure_env.get_article_path(), "validation_set.jsonl"), "w+") as f:
            for t in validation_set:
                f.write(json.dumps(t) + "\n")


if __name__ == "__main__":
    azure_env = AzureEnv(stage="dev", brand="clo3d", language="English")
    fine_tuning = FineTuning(azure_env)
    fine_tuning.fine_tune()
