# Task 2: OpenAI API Chatbot

## Purpose

In this task, you move from local inference to hosted model inference using the OpenAI API. You also practice secure key loading with `.env`.

## What You Learn

- making HTTP requests to OpenAI from Python,
- loading secrets with `python-dotenv`,
- keeping API keys out of source code.

## Files

- `chatbot-OpenAI.py`: OpenAI-only command-line chatbot.
- `example.env`: example environment file.
- `.env`: your local secret file (not committed).
- `requirements.txt`: task dependencies.

## Prerequisites

- Python `3.10+`
- OpenAI API key

## Setup

1. Install dependencies.

```bash
pip install -r requirements.txt
```

2. Create a `.env` file in this folder.

```env
OPENAI_API_KEY=your_key_here
```

You can copy `example.env` and replace the placeholder value.

## Run

From this folder:

```bash
python chatbot-OpenAI.py
```

Optional model override:

```bash
python chatbot-OpenAI.py --model gpt-5-nano
```

Type `exit` or `quit` to stop.

## Checkpoint

Before moving to Task 3, confirm you can:

- load `OPENAI_API_KEY` from `.env`,
- send a prompt,
- receive a valid response from OpenAI.
