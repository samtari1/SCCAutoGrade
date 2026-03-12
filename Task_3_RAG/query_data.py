import argparse
import os
from langchain_community.vectorstores import Chroma
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

CHROMA_PATH = "chroma"

PROMPT_TEMPLATE = """
Answer the question based only on the following context:

{context}

---

Answer the question based on the above context: {question}
"""


def load_openai_api_key():
    dotenv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(dotenv_path):
        try:
            from dotenv import load_dotenv
        except ImportError:
            # Continue without dotenv; key may still be set in shell environment.
            pass
        else:
            load_dotenv(dotenv_path)

    if not os.environ.get("OPENAI_API_KEY"):
        raise ValueError(
            "OPENAI_API_KEY is not set. Add it to Task_3_RAG/.env or export it in your shell."
        )


def main():
    load_openai_api_key()

    # Create CLI.
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "query_text",
        nargs="?",
        type=str,
        help="The query text. If omitted, you will be prompted.",
    )
    args = parser.parse_args()
    query_text = args.query_text or input("Enter query: ").strip()
    if not query_text:
        print("No query provided.")
        return

    # Prepare the DB.
    embedding_function = OpenAIEmbeddings()
    db = Chroma(persist_directory=CHROMA_PATH, embedding_function=embedding_function)

    # Search the DB.
    results = db.similarity_search_with_relevance_scores(query_text, k=3)
    if len(results) == 0 or results[0][1] < 0.7:
        print(f"Unable to find matching results.")
        return

    context_text = "\n\n---\n\n".join([doc.page_content for doc, _score in results])
    prompt_template = ChatPromptTemplate.from_template(PROMPT_TEMPLATE)
    prompt = prompt_template.format(context=context_text, question=query_text)
    print(prompt)

    model = ChatOpenAI()
    response = model.invoke(prompt)
    response_text = response.content

    sources = [doc.metadata.get("source", None) for doc, _score in results]
    formatted_response = f"Response: {response_text}\nSources: {sources}"
    print(formatted_response)


if __name__ == "__main__":
    main()
