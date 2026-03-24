from openai import OpenAI
from dotenv import load_dotenv
load_dotenv()

client = OpenAI()

model_name = "gpt-5-nano"
prompt = "Write a short bedtime story about a unicorn."

if hasattr(client, "responses"):
    response = client.responses.create(
        model=model_name,
        input=prompt
    )
    print(response.output_text)
else:
    # Fallback for older OpenAI Python SDK versions without Responses API.
    response = client.chat.completions.create(
        model=model_name,
        messages=[{"role": "user", "content": prompt}],
    )
    print(response.choices[0].message.content)
