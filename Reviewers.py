import google.generativeai as genai
import os
import json
import re

GEMINI_API_KEY = "AQ.Ab8RN6LEQAT7jjwCzdZpTUrnzWeWM37dI2oy9Zhj96tFjYAYtA"  # your Gemini API key
genai.configure(api_key=GEMINI_API_KEY)

model = genai.GenerativeModel("gemini-2.5-flash")


def analyse_code(diff_text):
    """Send code diff to Gemini and get back a list of issues."""

    prompt = f"""You are a senior software engineer doing a thorough code review.

Here is a code diff (+ means added lines, - means removed lines):

{diff_text}

Analyse ONLY the added lines (lines starting with +) for issues.

Find all problems across these categories:
- Bugs and logical errors
- Time/space complexity issues  
- Security vulnerabilities (SQL injection, hardcoded secrets, etc.)
- Missing edge cases (null checks, empty input, etc.)
- Bad practices and code style issues

Return your response as a JSON array ONLY. No explanation, no markdown, just raw JSON.
Each item in the array must have exactly these fields:
- "file": the filename where the issue is (from the diff header)
- "line": the line number (integer) where the issue is
- "severity": one of "critical", "warning", or "suggestion"
- "issue": a clear one-sentence description of the problem
- "fix": a concrete one-sentence fix

Example format:
[
  {{
    "file": "main.py",
    "line": 23,
    "severity": "critical",
    "issue": "Null pointer dereference possible when user is None",
    "fix": "Add a null check: if user is None: return early"
  }}
]

If there are no issues, return an empty array: []

IMPORTANT: Return ONLY the JSON array. No other text."""

    try:
        response = model.generate_content(prompt)
        raw_text = response.text.strip()
        print(f"🤖 DEBUG - Gemini Raw Reply: {raw_text}") # Add this line

        # Clean up response in case Gemini adds markdown backticks
        raw_text = re.sub(r'^```json\s*', '', raw_text)
        raw_text = re.sub(r'^```\s*', '', raw_text)
        raw_text = re.sub(r'\s*```$', '', raw_text)
        raw_text = raw_text.strip()

        issues = json.loads(raw_text)

        # Validate it's a list
        if not isinstance(issues, list):
            print("Gemini returned non-list response")
            return []

        # Validate each issue has required fields
        valid_issues = []
        required_fields = ['file', 'line', 'severity', 'issue', 'fix']
        for item in issues:
            if all(field in item for field in required_fields):
                valid_issues.append(item)
            else:
                print(f"Skipping malformed issue: {item}")

        print(f"Gemini found {len(valid_issues)} issues")
        return valid_issues

    except json.JSONDecodeError as e:
        print(f"Failed to parse Gemini response as JSON: {e}")
        print(f"Raw response: {raw_text}")
        return []

    except Exception as e:
        print(f"Gemini API error: {e}")
        return []