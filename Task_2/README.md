# Task 2

CLI chatbot that can switch between **Ollama** and **OpenAI**.

## Files

- `chatbot-OpenAI.py` — chatbot script with provider switching.
- `.env` — local secrets (not committed).
- `example.env` — sample env file format.

## Requirements

- Python 3.10+
- `python-dotenv`

Install dependency:

```bash
pip install python-dotenv
```

## Provider options

- `--provider ollama` (default)
- `--provider openai`

## Run examples

Use Ollama:

```bash
python chatbot-OpenAI.py --provider ollama
```

Use OpenAI:

1. Create an env file named `.env` (the filename must be exactly `.env`).

	You can either:

	- copy `example.env` to `.env`, or
	- create a new file named `.env` manually.

	Example value inside `.env`:

```env
OPENAI_API_KEY=your_key_here
```

2. Run:

```bash
python chatbot-OpenAI.py --provider openai
```

Optional model override:

```bash
python chatbot-OpenAI.py --provider openai --model gpt-5-nano
python chatbot-OpenAI.py --provider ollama --model llama3.2:1b
```

Type `exit` or `quit` to stop.
