# 🤖 AI Code Review Agent

An automated, AI-powered code review assistant that acts like a Senior Software Engineer. Built for GitLab, this agent listens for new Merge Requests, analyzes code diffs using Google's Gemini API, and posts structured, inline feedback directly on the PR.

## 🚀 Features
*   **Inline Code Comments:** Pinpoints exactly where the bug is and leaves a comment on the specific line in the GitLab UI.
*   **Smart Categorization:** Classifies issues into `Critical`, `Warning`, and `Suggestion`.
*   **Actionable Fixes:** Provides concrete, one-line code suggestions for every issue found.
*   **Summary Report:** Generates a top-level summary of the entire PR's health.

## 🛠️ Architecture
*   **Flask (`app.py`):** Webhook listener to intercept GitLab events.
*   **GitLab Integration (`Gitlab_helper.py`):** Fetches diffs and posts threaded comments using the GitLab API.
*   **AI Engine (`Reviewers.py`):** Prompts Gemini to analyze code and return strict JSON arrays.

## ⚙️ Setup & Installation

**1. Install Dependencies**
Ensure you have Python installed, then install the required libraries:
```bash
pip install -r req.txt
pip install pyngrok
```

**2. Set Environment Variables**
The agent securely reads API keys from your environment. Export them in your terminal session:
```bash
export GITLAB_TOKEN="your_gitlab_personal_access_token"
export GEMINI_API_KEY="your_gemini_api_key"
```

**3. Start the Agent**
Run the Flask server:
```bash
python app.py
```

**4. Expose Local Server (for testing)**
In a separate terminal, use ngrok to create a public URL:
```bash
ngrok http 5000
```

**5. Configure GitLab Webhook**
*   Go to your GitLab Repository -> **Settings** -> **Webhooks**.
*   Paste your ngrok URL and append `/webhook` (e.g., `https://xyz.ngrok-free.app/webhook`).
*   Check the box for **Merge request events**.
*   Save and click **Test**.

## 🎯 Where to Find the Output

Once a developer opens or updates a Merge Request, the agent will process the changes behind the scenes. 

1.  **Inline Comments (The Code):** Navigate to the **Changes** tab of your Merge Request in GitLab. You will see automated comments pinned directly to the specific lines of code that have bugs, security risks, or stylistic issues.
2.  **Executive Summary:** Navigate to the **Overview** tab of the Merge Request. Scroll to the bottom of the discussion thread to see the `🤖 AI Code Review Summary` block, which totals the critical issues and warnings.
3.  **Terminal Logs:** Check the terminal running `app.py` to see the real-time processing logs and the raw JSON output from the Gemini API.