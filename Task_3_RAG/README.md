# Task 3: RAG with LangChain + Chroma

## Purpose

In this task, you build a retrieval-augmented generation (RAG) pipeline. Instead of answering from model memory alone, the model uses retrieved document chunks as context.

This is the core pattern you will reuse in the final auto-grading project.

## What You Learn

- loading source documents,
- chunking and embedding text,
- storing vectors in Chroma,
- retrieving relevant context for grounded answers.

## Prerequisites

- Python `3.11` or `3.12` recommended
- OpenAI API key

## Setup

1. Install dependencies.

```bash
pip install -r requirements.txt
```

2. Create `.env` in this folder (or export in shell):

```env
OPENAI_API_KEY=your_key_here
```

## Step-by-Step

1. Build the vector database.

```bash
python create_database.py
```

2. Query with a direct question argument.

```bash
python query_data.py "How does Alice meet the Mad Hatter?"
```

3. Or run without an argument and type the question when prompted.

```bash
python query_data.py
```

## Expected Output

- a generated answer,
- a list of source files used by retrieval.

## Troubleshooting

- If you see `OPENAI_API_KEY is not set`, verify `Task_3_RAG/.env` exists and contains the key.
- If you see Python 3.14 compatibility warnings from LangChain/Pydantic, use Python 3.11 or 3.12 for this task.

## Checkpoint

Before starting the auto-grading integration, confirm you can:

- rebuild Chroma successfully,
- run at least two different queries,
- inspect source citations returned by the script.
