#!/usr/bin/env python3
import json
import os
from pathlib import Path
from typing import Any, Dict
import urllib.error
import urllib.request


def read_text_file(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()

def load_simple_env(env_path):
    if not os.path.isfile(env_path):
        return

    with open(env_path, "r", encoding="utf-8", errors="ignore") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def build_prompt(instructions_html: str, student_sql: str) -> str:
    return (
        "You are an expert MySQL instructor.\n"
        "Grade the SQL submission against the assignment instructions.\n\n"
        "Return STRICT JSON with this shape:\n"
        "{\n"
        "  \"final_score\": <number 0-100>,\n"
        "  \"summary\": \"short summary\",\n"
        "  \"issues\": [\"list of concrete SQL issues\"],\n"
        "  \"suggested_fixes\": [\"list of concrete fixes\"],\n"
        "  \"corrected_sql\": \"corrected SQL\"\n"
        "}\n\n"
        "ASSIGNMENT (HTML):\n"
        f"{instructions_html}\n\n"
        "STUDENT SUBMISSION (SQL):\n"
        f"{student_sql}"
    )

def extract_response_text(response_data: Dict[str, Any]) -> str:
    if isinstance(response_data.get("output_text"), str) and response_data["output_text"].strip():
        return response_data["output_text"]

    chunks = []
    for item in response_data.get("output", []):
        for content in item.get("content", []):
            text = content.get("text")
            if isinstance(text, str):
                chunks.append(text)
    return "\n".join(chunks).strip()


def call_openai(api_key: str, model: str, prompt: str, timeout_seconds: int) -> Dict[str, Any]:
    url = "https://api.openai.com/v1/responses"
    payload = {
        "model": model,
        "input": prompt,
    }

    req = urllib.request.Request(
        url=url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore") if hasattr(e, "read") else str(e)
        return {"error": f"OpenAI API HTTP {e.code}", "details": body}
    except Exception as e:
        return {"error": f"OpenAI API request failed: {e}"}

    text = extract_response_text(data)
    if not text:
        return {"error": "OpenAI returned empty output.", "raw": data}

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = text[start : end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    return {"raw_text": text}


def main() -> None:
    script_dir = Path(__file__).resolve().parent
    load_simple_env(script_dir / ".env")
    load_simple_env(script_dir.parent / "Prototype" / ".env")

    provider = os.getenv("MODEL_PROVIDER", "openai").strip().lower()
    model = os.getenv("MODEL_NAME", "gpt-5.4").strip()
    timeout_seconds = int(os.getenv("OPENAI_TIMEOUT_SECONDS", "240"))
    instructions_path = os.getenv("INSTRUCTIONS_FILE", str(script_dir / "Lab Assignment.html"))
    submission_path = os.getenv("SUBMISSION_FILE", str(script_dir / "CreateViews.sql"))
    save_path = os.getenv("GRADING_OUTPUT_FILE", str(script_dir / "grading_result.json"))

    if provider != "openai":
        print(f"MODEL_PROVIDER={provider} is not supported in this minimal script. Using OpenAI.")

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not set")

    instructions_html = read_text_file(instructions_path)
    student_sql = read_text_file(submission_path)
    prompt = build_prompt(instructions_html, student_sql)

    result = call_openai(api_key=api_key, model=model, prompt=prompt, timeout_seconds=timeout_seconds)

    print(json.dumps(result, indent=2, ensure_ascii=False))

    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"Saved: {save_path}")


if __name__ == "__main__":
    main()
