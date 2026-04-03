import argparse
import json
import os
import urllib.error
import urllib.request

from dotenv import load_dotenv

OPENAI_URL = "https://api.openai.com/v1/responses"
DEFAULT_OPENAI_MODEL = "gpt-5-nano"


def chat_openai(messages, model):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY environment variable is not set.")

    payload = {
        "model": model,
        "input": messages[-1]["content"],
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
        return parsed["output"][0]["content"]["text"]


def chat(messages, model):
    return chat_openai(messages, model)


def parse_args():
    parser = argparse.ArgumentParser(description="CLI chatbot for OpenAI")
    parser.add_argument(
        "--model",
        default=DEFAULT_OPENAI_MODEL,
        help="OpenAI model to use (default: gpt-5-nano)",
    )
    return parser.parse_args()


def main():
    dotenv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    load_dotenv(dotenv_path)

    args = parse_args()
    model = args.model

    print(f"CLI Chatbot (openai, model: {model})")
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
            reply = chat(messages, model=model)
            print(f"Bot: {reply}\n")
            messages.append({"role": "assistant", "content": reply})
        except ValueError as error:
            print(f"{error}\n")
            break
        except urllib.error.HTTPError as error:
            details = error.read().decode("utf-8", errors="replace")
            print(f"OpenAI API error ({error.code}): {details}\n")
        except urllib.error.URLError:
            print("Could not connect to OpenAI API. Check your network connection.\n")
        except (KeyError, json.JSONDecodeError):
            print("Received an unexpected response from OpenAI.\n")


if __name__ == "__main__":
    main()
