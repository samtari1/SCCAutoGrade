# SCCAutoGrade

Auto Grading Project

## Ollama CLI Chatbot Setup

This repository includes a simple command-line chatbot script: `chatbot.py`.

### 1) Install Ollama (macOS)

Using Homebrew:

```bash
brew install ollama
```

Or download from the official site:

- https://ollama.com/download

### 2) Start Ollama

```bash
ollama serve
```

Keep this terminal running while you chat.

### 3) Pull the model used by the script

The script currently uses `llama3.2:1b`, so run:

```bash
ollama pull llama3.2:1b
```

### 4) Run the chatbot script

From the project root:

```bash
python chatbot.py
```

Type messages at the prompt. Use `exit` or `quit` to stop.

### Troubleshooting

- If you see connection errors, ensure `ollama serve` is running.
- Default Ollama API URL is `http://localhost:11434`.
