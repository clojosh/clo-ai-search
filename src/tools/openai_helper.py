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

    def _create_chat_completion(self, *, max_tokens: int, **kwargs):
        return self.openai_client.chat.completions.create(
            **kwargs,
            max_completion_tokens=max_tokens,
        )

    def get_transcript_prompt(self, title: str, transcript: str) -> str:
        return f"""
    ### ROLE

    You are an expert Content Engineer specializing in NLP and RAG
    (Retrieval-Augmented Generation) data preparation.

    ### TASK

    Transform the provided raw YouTube transcript into a concise, structured
    Markdown knowledge document optimized for semantic search and vector embeddings.

    ### VIDEO CONTEXT

    Title: {title}

    ### INSTRUCTIONS

    1. CLEAN & PUNCTUATE
    Correct obvious transcription errors, punctuation, capitalization, and grammar.

    2. LIGHTLY COMPRESS
    Remove filler words, repeated statements, unnecessary conversational language,
    off-topic tangents, introductions, outros, calls to action, and sponsor segments.

    Condense repetitive explanations while preserving all meaningful information.

    Do NOT reduce the transcript to a high-level summary.

    3. PRESERVE INFORMATION
    Retain:
    - Important facts and claims
    - Technical explanations
    - Examples
    - Procedures and instructions
    - Numbers, measurements, dates, and specifications
    - Product, company, and technology names
    - Important warnings, caveats, and limitations

    4. SEMANTIC CHUNKING
    Break the content into logical sections using descriptive Markdown headers
    (## and ###).

    Each section should focus on one cohesive topic and should be understandable
    without requiring excessive context from previous sections.

    Prefer sections of approximately 200-500 words when practical.

    5. SECTION SUMMARIES
    Begin each major section with a concise 1-2 sentence explanation of what the
    section covers. This should contain the important terminology someone might
    use when searching for this information.

    6. PRESERVE TECHNICAL TERMS
    Ensure industry-specific terminology, brand names, software names, commands,
    technical specifications, and acronyms are spelled correctly.

    7. REMOVE TRANSCRIPT ARTIFACTS
    Remove timestamps, false starts, verbal fillers, repeated phrases, and
    transcription artifacts unless they contain meaningful information.

    ### OUTPUT REQUIREMENTS

    Return only the cleaned and structured Markdown document.

    Do not mention these instructions.

    Do not use the word "markdown" anywhere in the returned document.

    ### TRANSCRIPT

    {transcript}
    """

    def get_translation_prompt(self, text: str, target_language: str) -> str:
        return f"""### ROLE
You are a professional translator specializing in technical content.

### TASK
Translate the provided text into {target_language} while preserving the original meaning, technical terms, and context. Only translate the text, do not add any additional commentary or information.

### TEXT TO TRANSLATE
{text}"""

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
        chat_completion = self._create_chat_completion(
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
    def generate_structured_transcript(self, title: str, transcript: str) -> str:
        """
        Generate structured content from a transcript.

        Args:
            title (str): The title of the video
            transcript (str): The text of the transcript

        Returns:
            str: Structured content from the transcript
        """

        # If the transcript is too long, trim it to a length that OpenAI can handle
        tokens = num_tokens_from_string(transcript, "gpt-4")

        if tokens >= GPT_4_MINI_MAX_INPUT_TOKENS:
            transcript = transcript[:GPT_4_MINI_MAX_INPUT_TOKENS]

        # Create the prompt for the AI
        messages: list[ChatCompletionMessageParam] = [
            {
                "role": "user",
                "content": self.get_transcript_prompt(title, transcript),
            }
        ]

        # Ask the AI to generate structured content.
        chat_completion = self._create_chat_completion(model=self.AZURE_OPENAI_CHATGPT_DEPLOYMENT, messages=messages, temperature=0.7, max_tokens=2000, n=1)

        # Extract the structured content from the response.
        structured_content = chat_completion.choices[0].message.content
        if not structured_content:
            return ""

        structured_content = re.sub(r"\bmarkdown\b", "", structured_content, flags=re.IGNORECASE)
        structured_content = re.sub(r"\n+", " ", structured_content)
        structured_content = re.sub(r"\s+", " ", structured_content)

        return structured_content

    @retry(wait=wait_random_exponential(min=1, max=20), stop=stop_after_attempt(6))
    def generate_translation(self, text: str, target_language: str) -> str:
        """
        Translate a given text to a target language.

        Args:
            text (str): The text to translate.
            target_language (str): The language to translate the text into.

        Returns:
            str: The translated text.

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
            {"role": "user", "content": self.get_translation_prompt(text, target_language)},
        ]

        # Use the OpenAI API to generate the translation
        chat_completion = self._create_chat_completion(
            model=self.AZURE_OPENAI_CHATGPT_DEPLOYMENT,
            messages=messages,
            temperature=0.7,
            max_tokens=GPT_4_MINI_MAX_OUTPUT_TOKENS,
            n=1,
        )

        # Extract the generated translation from the response
        translation = chat_completion.choices[0].message.content
        if not translation:
            return ""

        return translation

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
        chat_completion = self._create_chat_completion(model=self.AZURE_OPENAI_CHATGPT_DEPLOYMENT, messages=messages, temperature=0.7, max_tokens=1000, n=1)

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
            chat_completion = self._create_chat_completion(model=self.AZURE_OPENAI_CHATGPT_DEPLOYMENT, messages=messages, temperature=0, max_tokens=1500, n=1)

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

            chat_completion = self._create_chat_completion(model=self.AZURE_OPENAI_CHATGPT_DEPLOYMENT, messages=messages, temperature=0, max_tokens=1500, n=1)

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

        chat_completion = self._create_chat_completion(model=self.AZURE_OPENAI_CHATGPT_DEPLOYMENT, messages=messages, temperature=0.7, max_tokens=50, n=1)

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

        chat_completion = self._create_chat_completion(model=self.AZURE_OPENAI_CHATGPT_DEPLOYMENT, messages=messages, temperature=0.7, max_tokens=50, n=1)

        outline = chat_completion.choices[0].message.content
        # outline = re.sub(r"\n+", " ", outline)
        # outline = re.sub(r"\s+", " ", outline)

        return outline
