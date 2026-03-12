# Task 1: Local LLM Chatbot (Ollama)

## Purpose

In this task, you run a chatbot fully on your own machine using Ollama. This teaches the local model workflow before using cloud APIs.

## What You Learn

- running a local LLM runtime,
- sending prompts from Python,
- handling a basic CLI chat loop.

## Files

- `chatbot.py`: Ollama-only command-line chatbot.

## Prerequisites

- Python `3.10+`
- Ollama installed: https://ollama.com/download

## Step-by-Step

1. Start Ollama in a terminal.

```bash
ollama serve
```

2. Pull the default model used by this task.

```bash
ollama pull llama3.2:1b
```

3. In this folder, run the chatbot.

```bash
python chatbot.py
```

4. Chat with the bot. Type `exit` or `quit` to stop.

## Optional

Use a different local model:

```bash
python chatbot.py --model llama3.2:3b
```

## Checkpoint

Before moving to Task 2, confirm you can:

- start Ollama,
- run the script,
- receive at least one response from the local model.
