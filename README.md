# 🤖 AI Code Review Agent

An autonomous, AI-powered code review agent that acts like a Senior Software Engineer. Built for GitLab, this agent listens for Merge Request, Push, and Note (mention) events, deeply analyzes code using Google's **Gemini 2.5 Flash** model via a live tool-calling loop, and posts structured inline and summary feedback directly into GitLab.

## 🚀 Features

- **Multi-Event Support:** Handles Merge Request hooks, Push hooks, and Note hooks (`@review-bot` mentions).
- **Inline Code Comments:** Pinpoints exact lines and posts severity-labelled findings (`Critical`, `High`, `Medium`, `Low`) directly on the diff in the GitLab UI.
- **Context-Aware Reviews:** Reads the project README first, then fetches full file contents and repo structure — not just the diff — before forming any opinion.
- **CI/CD Awareness:** Checks pipeline status before summarizing a Merge Request.
- **@review-bot Mentions:** Tag `@review-bot` in any MR, Issue, or Commit comment to trigger an on-demand review or answer a specific question.
- **Actionable Fixes:** Every finding includes a concrete suggested fix.
- **Summary Report:** Posts a final top-level verdict (`Approved` / `Approved with Suggestions` / `Changes Requested`) with a grouped severity breakdown.
- **Resilient API Calls:** Exponential backoff retry on Gemini 503/429 errors (up to 15 attempts).

## 🛠️ Architecture

```
app.py          →  Flask webhook listener (non-blocking, hands off to background thread)
Reviewers.py    →  Agentic core: Gemini 2.5 Flash + MCP + custom tool loop
```

- **Flask (`app.py`):** Receives GitLab webhook POST requests, identifies the event type, and dispatches processing to a background thread to avoid GitLab's 10-second timeout.
- **AI Engine (`Reviewers.py`):** Builds a combined tool set from the GitLab MCP server and custom Python tools, then runs an autonomous tool-calling loop where Gemini decides which tools to call and in what order until the review is complete.

### Tool Set

The agent has access to two layers of tools:

**Custom Python Tools** (direct GitLab REST API calls):

| Tool | Purpose |
|---|---|
| `get_readme` | Fetches the project README — always called first for context |
| `get_mr_diff` | Fetches the full code diff for a Merge Request |
| `get_commit_diff` | Fetches the diff for a specific commit SHA |
| `get_file_content` | Reads full file contents for context beyond the diff |
| `get_repo_tree` | Lists repository structure to discover related files |
| `get_pipeline_status` | Fetches CI/CD pipeline status for an MR |
| `post_mr_inline_comment` | Posts a severity-labelled inline comment on a specific line |
| `post_mr_comment` | Posts a general comment or final summary on an MR |
| `post_commit_comment` | Posts a review comment on a commit |

**GitLab MCP Server** (`@modelcontextprotocol/server-gitlab`): Additional GitLab operations exposed via the Model Context Protocol.

## ⚙️ Setup & Installation

### Prerequisites

- Python 3.9+
- Node.js & `npx` (required for the GitLab MCP server)

### 1. Install Dependencies

```bash
pip install -r req.txt
pip install pyngrok
pip install npm
```

`req.txt` includes:
```
flask==3.0.3
requests==2.32.3
google-generativeai==0.7.2
```

You will also need the MCP client library and the Google GenAI SDK:
```bash
pip install mcp google-genai
```

### 2. Set Environment Variables

The agent reads API keys from environment variables. Export them before starting:

```bash
export GITLAB_TOKEN="your_gitlab_personal_access_token"
export GEMINI_API_KEY="your_gemini_api_key"
```


### 3. Start the Agent

```bash
python app.py
```

The Flask server starts on port 5000.

### 4. Expose Local Server (for testing)

In a separate terminal, use ngrok to get a public URL:

```bash
ngrok http 5000
```

Copy the HTTPS forwarding URL (e.g. `https://xyz.ngrok-free.app`).

### 5. Configure GitLab Webhook

1. Go to your GitLab repository → **Settings** → **Webhooks**.
2. Set the URL to your ngrok address + `/webhook` (e.g. `https://xyz.ngrok-free.app/webhook`).
3. Enable the following trigger events:
   - ✅ **Merge request events**
   - ✅ **Push events**
   - ✅ **Comments** (for `@review-bot` mention support)
4. Save and click **Test** to verify connectivity.

## 🎯 How to Trigger a Review

### Automatic (Merge Request)
Open or update any Merge Request. The agent will automatically fetch context, analyze the diff, and post inline comments + a summary.

### Automatic (Push)
Push commits to any branch. The agent reviews the latest commit and posts a structured comment directly on it.

### On-Demand (Mention)
Leave a comment anywhere — an MR, Issue, or Commit — mentioning `@review-bot`:

```
@review-bot Can you check if this function handles null inputs correctly?
```

The agent will read the relevant diff or issue context and reply directly in the thread.

## 📍 Where to Find the Output

| Output | Location in GitLab |
|---|---|
| Inline findings | **MR → Changes tab** — comments pinned to specific lines |
| Final summary | **MR → Overview tab** — bottom of the discussion thread |
| Commit reviews | **Repository → Commits → [commit] → Comments** |
| Real-time logs | Terminal running `app.py` |

## 🔁 Review Workflow

The agent follows a fixed 4-step reasoning protocol on every event:

1. **README & Project Context** — reads the README to understand purpose, architecture, and any explicit coding rules.
2. **Gather Context** — fetches diffs, full file contents, repo tree, and pipeline status as needed.
3. **Review** — evaluates findings across: Correctness → Security → Performance → Reliability → Testing → Maintainability → Documentation consistency.
4. **Post Findings** — inline comments for line-level issues, then one final summary with an overall verdict.

## 🧪 Local Testing

A test harness is included at the bottom of `Reviewers.py`. To simulate a Push Hook locally without a real webhook:

```bash
python Reviewers.py
```

Adjust the `mock_payload` in the `__main__` block to point at a real project ID and commit SHA. A Merge Request Hook test payload is also included (commented out).