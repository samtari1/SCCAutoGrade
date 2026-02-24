# SCCAutoGrade

AI Auto-Grading Project for Sandhills Community College.

## Project Overview

This project explores how modern AI can support assignment grading in a practical, instructor-friendly way.

The goal is to build an app that helps professors:

- provide detailed and timely feedback to students,
- reduce repetitive grading workload,
- and gain a clearer high-level view of class performance.

This is a learning-focused project that starts simple and grows in complexity as the team gains confidence.

## Why This Matters

- **For students:** faster feedback and better guidance for improvement.
- **For faculty:** better insight into class trends with less manual effort.
- **For the project team:** hands-on experience applying AI responsibly to a real educational workflow.

## Collaboration & Communication

Most day-to-day communication happens in the course MS Teams channel.

Students are encouraged to:

- ask questions early,
- share ideas and blockers,
- and help peers troubleshoot setup and implementation issues.

When needed, additional meetings can be scheduled in person or via Zoom/Teams.

## Repository Structure

This repository is organized as incremental tasks.

- `Task_1/` introduces a basic local LLM chatbot workflow.
- `Task_2/` extends the chatbot to support provider switching (Ollama/OpenAI).

Each task folder contains its own `README.md` with task-specific instructions.

## Current Starter Scope

The current starter code helps students:

1. install and run a local LLM runtime (Ollama),
2. call model APIs from Python,
3. and build a simple CLI chatbot foundation for later auto-grading features.

## Quick Start (Starter Chatbot)

### 1) Install Ollama (macOS)

```bash
brew install ollama
```

Or download from: https://ollama.com/download

### 2) Start Ollama

```bash
ollama serve
```

### 3) Pull the default local model

```bash
ollama pull llama3.2:1b
```

### 4) Run from project root

```bash
python chatbot.py
```

You can also choose provider/model explicitly:

```bash
python chatbot.py --provider ollama
python chatbot.py --provider openai --model gpt-4o-mini
```

Type `exit` or `quit` to stop.

## Next Step for Students

Once your local setup is running successfully, post your status in the MS Teams group so the class can move to the next project phase together.
