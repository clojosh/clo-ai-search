import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import AzureOpenAI
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionUserMessageParam
from tenacity import retry, stop_after_attempt, wait_random_exponential

from .misc import trim_tokens

backend_dir = Path(__file__).parent.parent.parent

sys.path.append(str(backend_dir))
from tools.misc import num_tokens_from_string

# https://learn.microsoft.com/en-us/azure/ai-services/openai/concepts/models?tabs=python-secure%2Cglobal-standard%2Cstandard-chat-completions#gpt-4o-and-gpt-4-turbo
GPT_4_MINI_MAX_INPUT_TOKENS = 128000
GPT_4_MINI_MAX_OUTPUT_TOKENS = 16000
EMBEDDING_ADA_002_MAX_INPUT_TOKENS = 8191

TRANSCRIPT_SUMMARY_PROMPT = """You are an expert summarizer. Given a transcript of a YouTube video, generate a comprehensive summary that accurately reflects the key points, themes, and insights presented in the video. 
Instructions:
1. Identify the main topic and purpose of the video
2. Break down the content into clear sections or segments (e.g., introduction, key points, conclusion)
3. Extract and summarize important facts, arguments, or insights shared by the speaker(s)
4. Ignore filler content like greetings, off-topic tangents, or promotional content
5. Use clear and concise language suitable for downstream use in a retrieval-augmented generation (RAG) system.
6. If the transcript is too short or lacks sufficient detail, return an empty string.

###Transcript:
{transcript}"""


class OpenAIHelper:
    def __init__(
        self,
        openai_client: AzureOpenAI,
        AZURE_OPENAI_CHATGPT_DEPLOYMENT,
        AZURE_OPENAI_EMB_DEPLOYMENT,
        language="English",
    ):
        self.openai_client = openai_client
        self.AZURE_OPENAI_CHATGPT_DEPLOYMENT = AZURE_OPENAI_CHATGPT_DEPLOYMENT
        self.AZURE_OPENAI_EMB_DEPLOYMENT = AZURE_OPENAI_EMB_DEPLOYMENT
        self.language = language

    @retry(wait=wait_random_exponential(min=1, max=20), stop=stop_after_attempt(6))
    def generate_embeddings(self, text: str) -> list[float]:
        """
        Generate embeddings for a given text.

        Args:
            text (str): The text to generate embeddings for.

        Returns:
            list[float]: The generated embeddings.

        Raises:
            openai.error.OpenAIError: If the request to the OpenAI API fails.
        """
        tokens = num_tokens_from_string(text, "text-embedding-ada-002")

        if tokens >= EMBEDDING_ADA_002_MAX_INPUT_TOKENS:
            text = text[:EMBEDDING_ADA_002_MAX_INPUT_TOKENS]

        return (
            self.openai_client.embeddings.create(
                input=[text],
                model=self.AZURE_OPENAI_EMB_DEPLOYMENT,
            )
            .data[0]
            .embedding
        )

    @retry(wait=wait_random_exponential(min=1, max=20), stop=stop_after_attempt(6))
    def generate_questions(self, text: str) -> str:
        """
        Generate questions from a given text.

        Args:
            text (str): The text to generate questions from.

        Returns:
            str: The generated questions.

        Raises:
            openai.error.OpenAIError: If the request to the OpenAI API fails.
        """
        # Check if the text length exceeds the maximum allowed input length
        tokens = num_tokens_from_string(text, "gpt-4")

        if tokens >= GPT_4_MINI_MAX_INPUT_TOKENS:
            # Trim the text to the maximum allowed length
            text = text[:GPT_4_MINI_MAX_INPUT_TOKENS]

        # Create a list of messages to send to the OpenAI API
        messages: list[ChatCompletionMessageParam] = [
            {"role": "user", "content": f"Create a question that the following text addresses: {text}"},
        ]

        # Use the OpenAI API to generate the questions
        chat_completion = self.openai_client.chat.completions.create(
            model=self.AZURE_OPENAI_CHATGPT_DEPLOYMENT,
            messages=messages,
            temperature=0.7,
            max_tokens=50,
            n=1,
        )

        # Extract the generated questions from the response
        questions = chat_completion.choices[0].message.content
        if not questions:
            return ""

        # Remove any numbers at the start of each line
        questions = re.sub("^[0-9]+\.\s", "", questions, flags=re.MULTILINE)

        # Replace any newlines with spaces
        questions = re.sub("\n+", " ", questions, flags=re.MULTILINE)

        return questions

    @retry(wait=wait_random_exponential(min=1, max=20), stop=stop_after_attempt(6))
    def generate_transcript_summary(self, transcript: str) -> str:
        """
        Summarize a transcript

        Args:
            transcript (str): The text of the transcript to summarize

        Returns:
            str: A summary of the transcript
        """

        # If the transcript is too long, trim it to a length that OpenAI can handle
        tokens = num_tokens_from_string(transcript, "gpt-4")

        if tokens >= GPT_4_MINI_MAX_INPUT_TOKENS:
            transcript = transcript[:GPT_4_MINI_MAX_INPUT_TOKENS]

        # Create the prompt for the AI
        messages: list[ChatCompletionMessageParam] = [
            {
                "role": "user",
                "content": TRANSCRIPT_SUMMARY_PROMPT.format(transcript=transcript),
            }
        ]

        # Ask the AI to generate a summary
        chat_completion = self.openai_client.chat.completions.create(
            model=self.AZURE_OPENAI_CHATGPT_DEPLOYMENT, messages=messages, temperature=0.7, max_tokens=2000, n=1
        )

        # Extract the summary from the response
        summary = chat_completion.choices[0].message.content
        if not summary:
            return ""

        summary = re.sub(r"\n+", " ", summary)
        summary = re.sub(r"\s+", " ", summary)

        return summary

    @retry(wait=wait_random_exponential(min=1, max=20), stop=stop_after_attempt(6))
    def generate_pdf_summary(self, pdf):
        """
        Summarize a PDF

        Args:
            pdf (str): The text of the PDF to summarize

        Returns:
            str: A summary of the PDF
        """
        # If the PDF is too long, trim it to a length that OpenAI can handle
        tokens = num_tokens_from_string(pdf, "gpt-4")

        if tokens >= GPT_4_MINI_MAX_INPUT_TOKENS:
            pdf = pdf[:GPT_4_MINI_MAX_INPUT_TOKENS]

        # Create the prompt for the AI
        messages: list[ChatCompletionMessageParam] = [
            {
                "role": "user",
                "content": f"Provide a comprehensive guide of the given text. Include all step-by-step instructions, definitions, and warranties. {pdf}",
            }
        ]

        # Ask the AI to generate a summary
        chat_completion = self.openai_client.chat.completions.create(
            model=self.AZURE_OPENAI_CHATGPT_DEPLOYMENT, messages=messages, temperature=0.7, max_tokens=1000, n=1
        )

        # Extract the summary from the response
        summary = chat_completion.choices[0].message.content
        summary = re.sub(r"\n+", " ", summary)
        summary = re.sub(r"\s+", " ", summary)

        return summary

    @retry(wait=wait_random_exponential(min=1, max=20), stop=stop_after_attempt(6))
    def outline_webpage(self, content, website_url):
        """
        Outlines a webpage based on the content of the webpage.

        Args:
            content (str): The HTML content of the webpage
            website_url (str): The URL of the webpage

        Returns:
            str: A detailed outline of the webpage
        """
        try:
            # The maximum amount of tokens that can be processed by the AI is 32,000 - 1,500
            # If the content is longer than this, trim it to this length
            tokens = num_tokens_from_string(content, "gpt-4")

            if tokens >= GPT_4_MINI_MAX_INPUT_TOKENS:
                raise ValueError(f"Content too long, tokens found {tokens}")

            # Create a prompt for the AI
            messages = [
                {
                    "role": "user",
                    "content": f"""
                        Provide a detailed outline of a website based on the provided HTML code.
                        Outline must include all text and web links whereever possible, for example:
                        [Start Free Trial](https://clo3d.com).
                        The outline must exclude any html tags.
                        The url of the website is {website_url}.
                        ###HTML Code###: {content}
                    """,
                }
            ]

            # Ask the AI to generate an outline
            chat_completion = self.openai_client.chat.completions.create(
                model=self.AZURE_OPENAI_CHATGPT_DEPLOYMENT, messages=messages, temperature=0, max_tokens=1500, n=1
            )

            # Extract the outline from the response
            outline = chat_completion.choices[0].message.content
            outline = re.sub(r"\[https.*\]", "", outline)
            outline = outline.replace("[", "").replace("]", "").replace("(", "[").replace(")", "]")

            return outline
        except Exception as e:
            print("OpenAI Outline Webpage Error: ", e)
            return ""

    @retry(wait=wait_random_exponential(min=1, max=20), stop=stop_after_attempt(6))
    def scrape_webpage(self, content, website_url):
        """Scrape a Webpage"""

        try:
            tokens = num_tokens_from_string(content, "gpt-4")

            if tokens >= GPT_4_MINI_MAX_INPUT_TOKENS:
                raise ValueError(f"Content too long for {website_url}, tokens found {tokens}")

            # messages = [{
            #     "role": "user",
            #     "content": f"Provide detailed instructions and web links to effectively navigate and utilize the features of a website based on the provided HTML code: {content}"
            # }]

            messages = [
                {
                    "role": "user",
                    "content": f"Provide detailed instructions to effectively navigate and utilize the features of a website based on the provided HTML code. Instructions must include web links from the provided HTML code, for example: [Start Free Trial](https://clo3d.com). Instructions must exclude any html tags. The url of the website is {website_url}. ###HTML Code###: {content}",
                }
            ]

            chat_completion = self.openai_client.chat.completions.create(
                model=self.AZURE_OPENAI_CHATGPT_DEPLOYMENT, messages=messages, temperature=0, max_tokens=1500, n=1
            )

            scraped_content = chat_completion.choices[0].message.content
            scraped_content = re.sub(r"\[https.*\]", "", scraped_content)
            scraped_content = scraped_content.replace("[", "").replace("]", "").replace("(", "[").replace(")", "]")

            return scraped_content
        except Exception as e:
            print("OpenAI Scrape Webpage Error: ", e)
            return ""

    @retry(wait=wait_random_exponential(min=1, max=20), stop=stop_after_attempt(6))
    def create_webpage_title(self, content):
        """
        Creates a title for a webpage based on the content of the webpage.

        Args:
            content (str): The HTML content of the webpage

        Returns:
            str: A title for the webpage
        """

        tokens = num_tokens_from_string(content, "gpt-4")

        if tokens >= GPT_4_MINI_MAX_INPUT_TOKENS:
            raise ValueError(f"Content too long, tokens found {tokens}")

        messages = [{"role": "user", "content": f"Generate a concise and short title for a web page based on the following content: {content}"}]

        chat_completion = self.openai_client.chat.completions.create(
            model=self.AZURE_OPENAI_CHATGPT_DEPLOYMENT, messages=messages, temperature=0.7, max_tokens=50, n=1
        )

        outline = chat_completion.choices[0].message.content
        # outline = re.sub(r"\n+", " ", outline)
        # outline = re.sub(r"\s+", " ", outline)

        return outline

    @retry(wait=wait_random_exponential(min=1, max=20), stop=stop_after_attempt(6))
    def create_webpage_description(self, content):
        """
        Creates a description for a webpage based on the content of the webpage.

        Args:
            content (str): The HTML content of the webpage

        Returns:
            str: A description for the webpage
        """

        tokens = num_tokens_from_string(content, "gpt-4")

        if tokens >= GPT_4_MINI_MAX_INPUT_TOKENS:
            raise ValueError(f"Content too long, tokens found {tokens}")

        messages = [{"role": "user", "content": f"Generate a short, one sentence purpose for a web page based on the following content: {content}"}]

        chat_completion = self.openai_client.chat.completions.create(
            model=self.AZURE_OPENAI_CHATGPT_DEPLOYMENT, messages=messages, temperature=0.7, max_tokens=50, n=1
        )

        outline = chat_completion.choices[0].message.content
        # outline = re.sub(r"\n+", " ", outline)
        # outline = re.sub(r"\s+", " ", outline)

        return outline
