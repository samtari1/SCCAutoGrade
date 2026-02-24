import argparse
import json
import os
import urllib.error
import urllib.request

from dotenv import load_dotenv

OLLAMA_URL = "http://localhost:11434/api/chat"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_OLLAMA_MODEL = "llama3.2:1b"
DEFAULT_OPENAI_MODEL = "gpt-5-nano"


def chat_ollama(messages, model):
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=120) as response:
        body = response.read().decode("utf-8")
        parsed = json.loads(body)
        return parsed["message"]["content"]


def chat_openai(messages, model):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY environment variable is not set.")

    payload = {
        "model": model,
        "messages": messages,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        OPENAI_URL,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=120) as response:
        body = response.read().decode("utf-8")
        parsed = json.loads(body)
        return parsed["choices"][0]["message"]["content"]


def chat(messages, provider, model):
    if provider == "openai":
        return chat_openai(messages, model)
    return chat_ollama(messages, model)


def parse_args():
    parser = argparse.ArgumentParser(description="CLI chatbot for Ollama or OpenAI")
    parser.add_argument(
        "--provider",
        choices=["ollama", "openai"],
        default=os.environ.get("CHAT_PROVIDER", "ollama").lower(),
        help="Chat provider to use (default: CHAT_PROVIDER env var or ollama)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model to use (provider default if omitted)",
    )
    return parser.parse_args()


def main():
    dotenv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    load_dotenv(dotenv_path)

    args = parse_args()
    provider = args.provider
    model = args.model or (
        DEFAULT_OPENAI_MODEL if provider == "openai" else DEFAULT_OLLAMA_MODEL
    )

    print(f"CLI Chatbot ({provider}, model: {model})")
    print("Type 'exit' or 'quit' to stop.\n")

    messages = []

    while True:
        user_input = input("You: ").strip()

        if not user_input:
            continue

        if user_input.lower() in {"exit", "quit"}:
            print("Goodbye!")
            break

        messages.append({"role": "user", "content": user_input})

        try:
            reply = chat(messages, provider=provider, model=model)
            print(f"Bot: {reply}\n")
            messages.append({"role": "assistant", "content": reply})
        except ValueError as error:
            print(f"{error}\n")
            break
        except urllib.error.HTTPError as error:
            details = error.read().decode("utf-8", errors="replace")
            print(f"{provider.title()} API error ({error.code}): {details}\n")
        except urllib.error.URLError:
            if provider == "ollama":
                print(
                    "Could not connect to Ollama. Make sure it is running on http://localhost:11434.\n"
                )
            else:
                print("Could not connect to OpenAI API. Check your network connection.\n")
        except (KeyError, json.JSONDecodeError):
            print(f"Received an unexpected response from {provider}.\n")


if __name__ == "__main__":
    main()
