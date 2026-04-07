# Task 4: Simple SQL Auto-Grading

## Purpose

This task uses a minimal grader script to evaluate a SQL submission against the lab instructions using the OpenAI API.

The script:

- loads the assignment HTML,
- loads one SQL submission,
- sends both to OpenAI,
- expects a grading response with score and feedback,
- saves the result as JSON.

## Files Used

- `AutoGrade.py` - minimal grader script
- `Lab Assignment.html` - grading instructions/rubric source
- `CreateViews.sql` - sample student submission
- `.env` - API and runtime configuration

## Prerequisites

- Python `3.10+`
- OpenAI API key

## Environment Setup

Create or update `.env` in this folder:

```env
OPENAI_API_KEY=your_openai_api_key_here
MODEL_PROVIDER=openai
MODEL_NAME=gpt-5.4
OPENAI_TIMEOUT_SECONDS=240
INSTRUCTIONS_FILE=Lab Assignment.html
SUBMISSION_FILE=CreateViews.sql
GRADING_OUTPUT_FILE=grading_result.json
```

## Run

From this folder:

```bash
python AutoGrade.py
```

## Output

The script prints the grading JSON and writes it to:

- `grading_result.json` (or the file path set in `GRADING_OUTPUT_FILE`)

Expected response structure:

- `final_score` (0-100)
- `summary`
- `issues`
- `suggested_fixes`
- `corrected_sql`

## Student Activity

Students should attempt the following:

1. Play with the code in `AutoGrade.py` (for example, prompt wording or output format constraints).
2. Play with the grading prompt in `build_prompt(...)` and observe how grading changes.
3. Ensure the grader returns a clear score out of 100 (`final_score`).
4. Compare at least two prompt versions and document which prompt gives more consistent grading.

## Notes

- This is a minimal OpenAI-only version.
- If `MODEL_PROVIDER` is not `openai`, the script falls back to OpenAI mode.
- If you see `OPENAI_API_KEY is not set`, verify this folder's `.env` first.
