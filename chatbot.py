import json
import urllib.error
import urllib.request

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "llama3.2:1b"

def chat(messages, model=MODEL):
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


def main():
    print("Ollama CLI Chatbot")
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
            reply = chat(messages)
            print(f"Bot: {reply}\n")
            messages.append({"role": "assistant", "content": reply})
        except urllib.error.URLError:
            print("Could not connect to Ollama. Make sure it is running on http://localhost:11434.\n")
        except (KeyError, json.JSONDecodeError):
            print("Received an unexpected response from Ollama.\n")


if __name__ == "__main__":
    main()
