# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This is a Python CLI toolset for ingesting content from multiple sources (Zendesk help articles, CLO-SET community posts, YouTube/Bilibili videos, PDF files, CLO API docs) and indexing them into Azure AI Search with vector embeddings for semantic/hybrid search.

## Environment Setup

```powershell
# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers (required for api/clo.py web scraping)
playwright install
```

**Environment files** (at repo root):
- `.env.dev` — dev Azure Search + OpenAI credentials
- `.env.prod` — prod credentials
- `.env.api.dev` — credentials for CLO API mode

Each `.env` file needs: `ZENDESK_USERNAME`, `ZENDESK_PASSWORD`, `AZURE_SEARCH_SERVICE`, `AZURE_SEARCH_KEY`, `AZURE_OPENAI_SERVICE`, `AZURE_OPENAI_KEY`, `AZURE_OPENAI_CHATGPT_DEPLOYMENT`, `AZURE_OPENAI_EMB_DEPLOYMENT`, `{BRAND}_AZURE_SEARCH_INDEX`, `MONGO_URI`, `{BRAND}_MONGO_DB_NAME`, etc.

## Running Scripts

All entry points use interactive `questionary` prompts — run directly with Python:

```powershell
python src/ingest/articles.py      # Zendesk help center articles
python src/ingest/posts.py         # CLO-SET community posts
python src/ingest/pdf.py           # PDF documents
python src/media/youtube/main.py   # YouTube transcripts
python src/media/bilibili.py       # Bilibili videos
python src/api/clo.py              # CLO API docs (uses Playwright)
python src/api/md.py               # Marvelous Designer API docs
python src/search/ai_search.py     # Azure AI Search index management
```

Each script prompts for: app type (`Chat Bot` or `CLO API`), stage (`dev`/`prod`), brand (`clo3d`, `closet`, `connect`, `md`, `allinone`), then task.

## Architecture

### Core pattern
Every module takes an `Azure` instance as its dependency. `Azure` (`src/tools/azure.py`) loads the correct `.env` file based on `app`/`stage`, then initializes `SearchClient`, `SearchIndexClient`, and `AzureOpenAI`. It also provides helpers for building Zendesk API URLs per brand/locale.

### Source modules
| Module | Source | Output |
|--------|--------|--------|
| `src/ingest/articles.py` | Zendesk Help Center API | `data/{brand}/articles/{locale}/` |
| `src/ingest/posts.py` | CLO-SET Community API | `data/{brand}/posts/page_N.json` |
| `src/ingest/pdf.py` | Local PDF files | Extracted text |
| `src/media/youtube/` | YouTube Data API + transcripts | `data/{brand}/youtube/` |
| `src/media/bilibili.py` | Bilibili API | `data/{brand}/bilibili/` |
| `src/api/clo.py` | CLO developer docs (Playwright scraping) | `data/clo3d/api/` |
| `src/api/md.py` | Marvelous Designer docs | `data/md/api/` |

### Search index
`src/search/ai_search.py` (`AISearch` class) manages the Azure Cognitive Search index: creates/drops indexes, uploads/deletes documents, and supports text, vector, hybrid, and semantic-vector search modes. Index schema: `article_id` (key), `url`, `title`, `content`, `content_description`, `source`, `created_at`, plus 1536-dim vectors `title_vector` and `content_vector` using HNSW.

### Brands
`clo3d`, `closet`, `connect`, `md` (Marvelous Designer), `allinone` (merged index). The `allinone` brand aggregates posts from all other brand folders before uploading.

### Utilities (`src/tools/`)
- `azure.py` — `Azure` class: env loading, client init, Zendesk URL builders per brand/locale
- `openai_helper.py` — `OpenAIHelper`: embedding generation, content description creation
- `misc.py` — HTML→Markdown conversion, token trimming, language detection (lingua), image filtering

### YouTube module structure (`src/media/youtube/`)
Split into: `api.py` (YouTube Data API calls), `videos.py` (video management), `transcripts.py` (transcript extraction), `subtitles.py` (subtitle generation via faster-whisper/GPU), `constants.py` (channel IDs, exclusion lists), `main.py` (orchestration entry point).

## Data Flow

1. **Fetch** — scripts pull content from external APIs or scrape web pages
2. **Store** — raw/processed content saved as JSON under `data/{brand}/{type}/`
3. **Upload** — documents are embedded via Azure OpenAI and uploaded to Azure AI Search via `mergeOrUpload`
