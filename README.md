# SCCAutoGrade

AI Auto-Grading project for Sandhills Community College.

## Learning Goal

This repository is designed as a step-by-step learning path. Students start with a local LLM, then call a hosted model API, then build a RAG pipeline. These pieces become the foundation of the final auto-grading project.

## Task Sequence

1. `Task_1_Local_LLM/`
   Learn local inference with Ollama and a Python CLI chatbot.
2. `Task_2_OpenAI_API/`
   Learn API-based inference with OpenAI, environment variables, and secret management.
3. `Task_3_RAG/`
   Learn retrieval-augmented generation (RAG) with Chroma + LangChain to answer using course documents.

Complete tasks in order. Each task README includes setup, run commands, and expected outcomes.

## Suggested Environment

- Python `3.11` or `3.12` (recommended for current LangChain compatibility)
- macOS, Linux, or Windows
- A terminal and basic Git/Python familiarity

## Final Project Direction

After Task 3, students should be ready to build an auto-grading workflow that:

- ingests assignment instructions and rubrics,
- retrieves relevant context,
- evaluates submissions against rubric criteria,
- produces transparent feedback with cited evidence.

## Collaboration

Use the course MS Teams channel for status updates, blockers, and design questions.

Students should share:

- what step they are on,
- command output when blocked,
- what they already tried.
