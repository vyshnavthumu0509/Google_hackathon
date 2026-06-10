import os
import time
import asyncio
import requests
from google import genai
from google.genai import types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def send_with_retry(chat, message, max_retries=15, base_wait=10):
    """
    Sends a message to the Gemini chat with exponential backoff retry
    on 503 (overloaded) and 429 (rate limit) errors.
    Covers BOTH the initial mission dispatch AND every tool-result reply.
    """
    for attempt in range(1, max_retries + 1):
        try:
            return chat.send_message(message)
        except Exception as e:
            err = str(e)
            is_retryable = "503" in err or "429" in err or "UNAVAILABLE" in err or "RESOURCE_EXHAUSTED" in err
            if is_retryable and attempt < max_retries:
                wait = base_wait * attempt  # 10s, 20s, 30s … up to ~150s
                print(f"⚠️  Gemini busy (attempt {attempt}/{max_retries}). Retrying in {wait}s…")
                time.sleep(wait)
            else:
                raise


# ── Credentials ────────────────────────────────────────────────────────────────
GEMINI_API_KEY = "AIzaSyC9fIzoFOuWss3VZHnzYiDyKZ6ys2Sn-s8"
GITLAB_TOKEN   = "glpat-fZ3N8BoJ387fadtq1lOnX2M6MQpvOjEKdTptd3Nrcw8.01.1709h8at4"

client   = genai.Client(api_key=GEMINI_API_KEY)
MODEL_ID = "gemini-2.5-flash"

# ── GitLab MCP Server ──────────────────────────────────────────────────────────
server_params = StdioServerParameters(
    command="npx",
    args=["-y", "@modelcontextprotocol/server-gitlab"],
    env={
        "GITLAB_PERSONAL_ACCESS_TOKEN": GITLAB_TOKEN,
        "GITLAB_API_URL": "https://gitlab.com/api/v4",
        "PATH": os.environ.get("PATH", "")
    }
)


# ══════════════════════════════════════════════════════════════════════════════
#  TOOL ROUTER  –  handles every custom Python tool the agent can call
# ══════════════════════════════════════════════════════════════════════════════
def handle_custom_tool(tool_name: str, tool_args: dict) -> str:
    headers = {"PRIVATE-TOKEN": GITLAB_TOKEN}

    # ── MR diff ───────────────────────────────────────────────────────────────
    if tool_name == "get_mr_diff":
        url = (f"https://gitlab.com/api/v4/projects/{tool_args['project_id']}"
               f"/merge_requests/{tool_args['mr_iid']}/changes")
        res = requests.get(url, headers=headers)
        if res.status_code == 200:
            changes = res.json().get("changes", [])
            return ("\n\n".join(f"File: {c['new_path']}\n{c['diff']}" for c in changes)
                    if changes else "No changes found.")
        return f"Failed to fetch MR diff. Status: {res.status_code}"

    # ── MR general comment ────────────────────────────────────────────────────
    elif tool_name == "post_mr_comment":
        url = (f"https://gitlab.com/api/v4/projects/{tool_args['project_id']}"
               f"/merge_requests/{tool_args['mr_iid']}/notes")
        res = requests.post(url, headers=headers, json={"body": tool_args["comment"]})
        return ("✅ MR comment posted successfully!"
                if res.status_code == 201
                else f"❌ Failed to post MR comment. Error: {res.text}")

    # ── Commit diff ───────────────────────────────────────────────────────────
    elif tool_name == "get_commit_diff":
        url = (f"https://gitlab.com/api/v4/projects/{tool_args['project_id']}"
               f"/repository/commits/{tool_args['commit_sha']}/diff")
        res = requests.get(url, headers=headers)
        if res.status_code == 200:
            diffs = res.json()
            return ("\n\n".join(f"File: {d['new_path']}\n{d['diff']}" for d in diffs)
                    if diffs else "No diff found for this commit.")
        return f"Failed to fetch commit diff. Status: {res.status_code}, Error: {res.text}"

    # ── Commit comment ────────────────────────────────────────────────────────
    elif tool_name == "post_commit_comment":
        url = (f"https://gitlab.com/api/v4/projects/{tool_args['project_id']}"
               f"/repository/commits/{tool_args['commit_sha']}/comments")
        res = requests.post(url, headers=headers, json={"note": tool_args["comment"]})
        return ("✅ Commit comment posted successfully!"
                if res.status_code == 201
                else f"❌ Failed to post commit comment. Error: {res.text}")

    # ── Full file content ─────────────────────────────────────────────────────
    elif tool_name == "get_file_content":
        encoded_path = requests.utils.quote(tool_args["file_path"], safe="")
        ref = tool_args.get("ref", "HEAD")
        url = (f"https://gitlab.com/api/v4/projects/{tool_args['project_id']}"
               f"/repository/files/{encoded_path}/raw?ref={ref}")
        res = requests.get(url, headers=headers)
        return res.text if res.status_code == 200 else f"Failed to fetch file. Status: {res.status_code}"

    # ── Pipeline / CI status ──────────────────────────────────────────────────
    elif tool_name == "get_pipeline_status":
        url = (f"https://gitlab.com/api/v4/projects/{tool_args['project_id']}"
               f"/merge_requests/{tool_args['mr_iid']}/pipelines")
        res = requests.get(url, headers=headers)
        return res.text if res.status_code == 200 else f"Failed to fetch pipeline. Status: {res.status_code}"

    # ── Inline MR comment (line-level) ────────────────────────────────────────
    elif tool_name == "post_mr_inline_comment":
        severity  = tool_args.get("severity", "Medium")
        body      = f"**[{severity}]** {tool_args['comment']}"
        diff_refs = tool_args.get("diff_refs", {})
        url = (f"https://gitlab.com/api/v4/projects/{tool_args['project_id']}"
               f"/merge_requests/{tool_args['mr_iid']}/discussions")
        payload = {
            "body": body,
            "position": {
                "position_type": "text",
                "new_path":  tool_args["file_path"],
                "new_line":  tool_args["line_number"],
                "base_sha":  diff_refs.get("base_sha",  ""),
                "head_sha":  diff_refs.get("head_sha",  ""),
                "start_sha": diff_refs.get("start_sha", ""),
            }
        }
        res = requests.post(url, headers=headers, json=payload)
        return ("✅ Inline comment posted!"
                if res.status_code == 201
                else f"❌ Failed to post inline comment. Error: {res.text}")

    # ── README / documentation fetch ──────────────────────────────────────────
    elif tool_name == "get_readme":
        for filename in ["README.md", "README.rst", "README.txt", "README"]:
            encoded = requests.utils.quote(filename, safe="")
            ref = tool_args.get("ref", "HEAD")
            url = (f"https://gitlab.com/api/v4/projects/{tool_args['project_id']}"
                   f"/repository/files/{encoded}/raw?ref={ref}")
            res = requests.get(url, headers=headers)
            if res.status_code == 200:
                return res.text
        return "No README found in the repository root."

    # ── List repository tree (to find related files) ──────────────────────────
    elif tool_name == "get_repo_tree":
        path = tool_args.get("path", "")
        ref  = tool_args.get("ref", "HEAD")
        url  = (f"https://gitlab.com/api/v4/projects/{tool_args['project_id']}"
                f"/repository/tree?path={path}&ref={ref}&recursive=false&per_page=50")
        res = requests.get(url, headers=headers)
        if res.status_code == 200:
            items = res.json()
            return "\n".join(f"{i['type']}: {i['path']}" for i in items)
        return f"Failed to fetch repo tree. Status: {res.status_code}"

    return f"Unknown custom tool: {tool_name}"


# ══════════════════════════════════════════════════════════════════════════════
#  TOOL DECLARATIONS  –  everything the agent may call
# ══════════════════════════════════════════════════════════════════════════════
CUSTOM_TOOL_DECLARATIONS = [
    types.FunctionDeclaration(
        name="get_mr_diff",
        description="Fetches the code diff for a specific Merge Request.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "project_id": {"type": "INTEGER"},
                "mr_iid":     {"type": "INTEGER"}
            },
            "required": ["project_id", "mr_iid"]
        }
    ),
    types.FunctionDeclaration(
        name="post_mr_comment",
        description="Posts a general code-review comment on a GitLab Merge Request.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "project_id": {"type": "INTEGER"},
                "mr_iid":     {"type": "INTEGER"},
                "comment":    {"type": "STRING"}
            },
            "required": ["project_id", "mr_iid", "comment"]
        }
    ),
    types.FunctionDeclaration(
        name="get_commit_diff",
        description="Fetches the diff for a specific commit SHA.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "project_id": {"type": "INTEGER"},
                "commit_sha": {"type": "STRING"}
            },
            "required": ["project_id", "commit_sha"]
        }
    ),
    types.FunctionDeclaration(
        name="post_commit_comment",
        description="Posts a review comment on a specific commit.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "project_id": {"type": "INTEGER"},
                "commit_sha": {"type": "STRING"},
                "comment":    {"type": "STRING"}
            },
            "required": ["project_id", "commit_sha", "comment"]
        }
    ),
    types.FunctionDeclaration(
        name="get_file_content",
        description=(
            "Fetches the full content of a specific file from the repository. "
            "Use this to get surrounding context beyond the diff."
        ),
        parameters={
            "type": "OBJECT",
            "properties": {
                "project_id": {"type": "INTEGER"},
                "file_path":  {"type": "STRING"},
                "ref":        {"type": "STRING", "description": "Branch name or commit SHA"}
            },
            "required": ["project_id", "file_path"]
        }
    ),
    types.FunctionDeclaration(
        name="get_pipeline_status",
        description="Fetches CI/CD pipeline status and test results for a Merge Request.",
        parameters={
            "type": "OBJECT",
            "properties": {
                "project_id": {"type": "INTEGER"},
                "mr_iid":     {"type": "INTEGER"}
            },
            "required": ["project_id", "mr_iid"]
        }
    ),
    types.FunctionDeclaration(
        name="post_mr_inline_comment",
        description=(
            "Posts an inline comment on a specific line of a file inside a Merge Request. "
            "Use for precise per-finding feedback with severity labels."
        ),
        parameters={
            "type": "OBJECT",
            "properties": {
                "project_id":  {"type": "INTEGER"},
                "mr_iid":      {"type": "INTEGER"},
                "file_path":   {"type": "STRING"},
                "line_number": {"type": "INTEGER"},
                "comment":     {"type": "STRING"},
                "severity": {
                    "type": "STRING",
                    "enum": ["Critical", "High", "Medium", "Low"]
                },
                "diff_refs": {
                    "type": "OBJECT",
                    "description": "base_sha, head_sha, start_sha from the MR diff refs",
                    "properties": {
                        "base_sha":  {"type": "STRING"},
                        "head_sha":  {"type": "STRING"},
                        "start_sha": {"type": "STRING"}
                    }
                }
            },
            "required": ["project_id", "mr_iid", "file_path", "line_number", "comment", "severity"]
        }
    ),
    types.FunctionDeclaration(
        name="get_readme",
        description=(
            "Fetches the project README (README.md / .rst / .txt). "
            "Always call this first to understand project purpose, architecture, "
            "coding conventions, and explicit review rules before reviewing any code."
        ),
        parameters={
            "type": "OBJECT",
            "properties": {
                "project_id": {"type": "INTEGER"},
                "ref":        {"type": "STRING", "description": "Branch or SHA (default HEAD)"}
            },
            "required": ["project_id"]
        }
    ),
    types.FunctionDeclaration(
        name="get_repo_tree",
        description=(
            "Lists files and directories in the repository (or a sub-path). "
            "Use to discover related files, test directories, and config files."
        ),
        parameters={
            "type": "OBJECT",
            "properties": {
                "project_id": {"type": "INTEGER"},
                "path": {"type": "STRING", "description": "Sub-directory path (empty = root)"},
                "ref":  {"type": "STRING", "description": "Branch or SHA (default HEAD)"}
            },
            "required": ["project_id"]
        }
    ),
]

SYSTEM_INSTRUCTION = """
You are an elite, autonomous GitLab Code Review Agent.

════════════════════════════════════════
STEP 0 — README & PROJECT CONTEXT FIRST
════════════════════════════════════════
Before touching any diff:
1. Call `get_readme` to read the project README.
2. Extract: project purpose, architecture, coding conventions, public APIs,
   feature requirements, and any explicit review rules stated in the README.
3. Use this as the lens for your entire review.

README rules:
- Treat it as context, NOT absolute truth.
- Compare code changes against documented requirements.
- If code and README diverge, report it as a suggestion — do NOT modify the README.
- Example GOOD finding: "The README describes JWT auth, but this PR introduces
  session-based auth. Consider updating docs if intentional."

════════════════════════════════════════
STEP 1 — GATHER CONTEXT
════════════════════════════════════════
- Fetch the full diff (get_mr_diff / get_commit_diff).
- Fetch full file contents (get_file_content) for any changed file where
  the diff alone is insufficient.
- Use get_repo_tree to discover related files, test folders, config files.
- Fetch pipeline / CI status (get_pipeline_status) for MR events.
- Never review solely from a small diff if more context can be retrieved.

════════════════════════════════════════
STEP 2 — REVIEW PRIORITIES (in order)
════════════════════════════════════════
1. Correctness   – bugs, edge cases, error handling, race conditions,
                   breaking API changes, state inconsistencies.
2. Security      – injection, auth/authz issues, secrets exposure,
                   unsafe deserialization, privilege escalation.
3. Performance   – N+1 queries, unnecessary allocations, complexity changes,
                   excessive network calls, inefficient loops.
4. Reliability   – retries, timeouts, graceful degradation, error propagation.
5. Testing       – missing coverage, untested edge cases; suggest specific
                   test cases with clear scenarios.
6. Maintainability – naming, modularity, duplication, project conventions,
                   refactoring opportunities.
7. Documentation consistency – flag drift between code and README/docs.

Only raise findings backed by clear evidence in the codebase.
Always reference the affected file and line number when possible.
Avoid speculative comments.

════════════════════════════════════════
STEP 3 — COMMENT STRATEGY
════════════════════════════════════════
- Use post_mr_inline_comment for specific line-level findings (preferred).
- Use post_mr_comment / post_commit_comment for general or cross-cutting issues.
- Every finding must include:
    • Severity: Critical | High | Medium | Low
    • Explanation (what is wrong and why it matters)
    • Suggested Fix (concrete code or approach)
- Group related issues together. Skip trivial nitpicks unless they affect
  maintainability or violate a project rule from the README.

════════════════════════════════════════
STEP 4 — FINAL SUMMARY COMMENT
════════════════════════════════════════
After all inline comments, post ONE final summary using post_mr_comment with:
- Overall verdict: Approved | Approved with Suggestions | Changes Requested
- Grouped bullet list of all findings by severity
- One-paragraph overall assessment
""".strip()


# ══════════════════════════════════════════════════════════════════════════════
#  ORCHESTRATION LOOP
# ══════════════════════════════════════════════════════════════════════════════
async def run_tool_loop(chat, mission: str, session):
    """Dispatch mission → run tool loop → return when agent stops calling tools."""
    response = send_with_retry(chat, mission)
    if response.text:
        print(f"🤖 Agent: {response.text}")

    while response.function_calls:
        for function_call in response.function_calls:
            tool_name = function_call.name
            tool_args  = function_call.args
            print(f"🛠️  Tool called: {tool_name}")

            # Route to custom Python handler or fall through to MCP
            if tool_name in {
                "get_mr_diff", "post_mr_comment",
                "get_commit_diff", "post_commit_comment",
                "get_file_content", "get_pipeline_status",
                "post_mr_inline_comment", "get_readme", "get_repo_tree",
            }:
                result_text = handle_custom_tool(tool_name, tool_args)
            else:
                mcp_result  = await session.call_tool(tool_name, arguments=tool_args)
                result_text = "\n".join(
                    c.text for c in mcp_result.content if c.type == "text"
                )

            snippet = result_text[:300] + ("…" if len(result_text) > 300 else "")
            print(f"📦 Result: {snippet}")

            response = send_with_retry(
                chat,
                types.Part.from_function_response(
                    name=tool_name, response={"result": result_text}
                )
            )
            if response.text:
                print(f"🤖 Agent: {response.text}")


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════
async def run_agent(event_type: str, payload: dict):
    print(f"🔌 Starting GitLab MCP Server for event: {event_type} …")

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            mcp_tools = await session.list_tools()

            # ── Build combined tool list (MCP + custom) ────────────────────
            gemini_tools = []
            for tool in mcp_tools.tools:
                clean_schema = tool.inputSchema.copy() if tool.inputSchema else {}
                clean_schema.pop("$schema", None)
                gemini_tools.append(
                    types.FunctionDeclaration(
                        name=tool.name,
                        description=tool.description,
                        parameters=clean_schema
                    )
                )
            gemini_tools.extend(CUSTOM_TOOL_DECLARATIONS)
            tool_config = types.Tool(function_declarations=gemini_tools)

            # ── Build mission string from event ────────────────────────────
            project_id = payload.get("project", {}).get("id") or payload.get("project_id")

            if event_type == "Merge Request Hook":
                mr_iid  = payload.get("object_attributes", {}).get("iid")
                mission = (
                    f"A Merge Request (#{mr_iid}) was updated in Project {project_id}. "
                    f"Start by calling get_readme to understand the project, then "
                    f"get_mr_diff to fetch the diff, fetch full file contents as needed "
                    f"with get_file_content, check get_pipeline_status, then post "
                    f"inline findings with post_mr_inline_comment and finish with a "
                    f"structured summary via post_mr_comment."
                )

            elif event_type == "Push Hook":
                ref       = payload.get("ref", "")
                branch    = ref.split("/")[-1] if ref else "unknown"
                commits   = payload.get("commits", [])
                after_sha = payload.get("after", "")

                if not after_sha or after_sha == "0" * 40:
                    print("⚠️  Branch deletion push — ignoring.")
                    return

                commit_messages = "; ".join(
                    f"'{c.get('message','').strip()}'" for c in commits[:5]
                )
                mission = (
                    f"{len(commits)} commit(s) pushed to branch '{branch}' "
                    f"in Project {project_id}. Latest SHA: {after_sha}. "
                    f"Messages: {commit_messages}. "
                    f"Start by calling get_readme to understand the project, then "
                    f"get_commit_diff for SHA {after_sha}, review thoroughly for bugs, "
                    f"security issues, and bad practices, then post_commit_comment "
                    f"with a structured review including severity labels and a final verdict."
                )

            elif event_type == "Note Hook":
                comment_body = payload.get("object_attributes", {}).get("note", "")
                mr_iid       = payload.get("merge_request", {}).get("iid")

                if "@review-bot" not in comment_body.lower():
                    print("💬 Comment does not mention @review-bot — ignoring.")
                    return

                mission = (
                    f"You were mentioned in a comment on MR #{mr_iid} in Project {project_id}. "
                    f"The user said: '{comment_body}'. "
                    f"Call get_readme for project context, get_mr_diff to read the code, "
                    f"then reply with post_mr_comment addressing the user's question directly."
                )

            else:
                print(f"⚠️  Unhandled event '{event_type}' — ignoring.")
                return

            # ── Initialise chat agent ──────────────────────────────────────
            chat = client.chats.create(
                model=MODEL_ID,
                config=types.GenerateContentConfig(
                    tools=[tool_config],
                    temperature=0.2,
                    system_instruction=SYSTEM_INSTRUCTION
                )
            )

            print(f"🚀 Mission dispatched:\n{mission}\n")
            await run_tool_loop(chat, mission, session)
            print("🎉 Mission complete!\n")


# ══════════════════════════════════════════════════════════════════════════════
#  LOCAL TEST HARNESS
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    # ── Test: Merge Request Hook ──
    # mock_payload = {
    #     "project": {"id": 12345678},
    #     "object_attributes": {"iid": 9}
    # }
    # asyncio.run(run_agent("Merge Request Hook", mock_payload))

    # ── Test: Push Hook ──
    mock_payload = {
        "project": {"id": 12345678},
        "ref": "refs/heads/main",
        "before": "abc123",
        "after": "def456",          # replace with a real commit SHA
        "commits": [
            {"message": "Fix login bug"},
            {"message": "Add input validation"}
        ]
    }
    print("🧪 Running local push event test …")
    asyncio.run(run_agent("Push Hook", mock_payload))