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
                raise  # non-retryable error or out of retries

# 1. Setup the Modern Gemini Client
# TODO: Insert your actual keys here!
GEMINI_API_KEY = "AIzaSyC9fIzoFOuWss3VZHnzYiDyKZ6ys2Sn-s8"
GITLAB_TOKEN = "glpat-fZ3N8BoJ387fadtq1lOnX2M6MQpvOjEKdTptd3Nrcw8.01.1709h8at4"


client = genai.Client(api_key=GEMINI_API_KEY)
MODEL_ID = "gemini-2.5-flash"

# 2. Configure the official GitLab MCP Server
server_params = StdioServerParameters(
    command="npx",
    args=["-y", "@modelcontextprotocol/server-gitlab"],
    env={
        "GITLAB_PERSONAL_ACCESS_TOKEN": GITLAB_TOKEN,
        "GITLAB_API_URL": "https://gitlab.com/api/v4",
        "PATH": os.environ.get("PATH", "")
    }
)

async def run_agent(event_type, payload):
    print(f"🔌 Spinning up GitLab MCP Server for event: {event_type}...")

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            mcp_tools = await session.list_tools()

            # 3. Convert MCP tools for Gemini
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

            # 🚀 THE HYBRID UPGRADE: Custom Python Tools for Code Review

            # --- MR Tools ---
            gemini_tools.append(
                types.FunctionDeclaration(
                    name="get_mr_diff",
                    description="Fetches the code diff for a specific Merge Request to review code changes.",
                    parameters={
                        "type": "OBJECT",
                        "properties": {
                            "project_id": {"type": "INTEGER"},
                            "mr_iid": {"type": "INTEGER"}
                        },
                        "required": ["project_id", "mr_iid"]
                    }
                )
            )
            gemini_tools.append(
                types.FunctionDeclaration(
                    name="post_mr_comment",
                    description="Posts a code review comment directly to a GitLab Merge Request.",
                    parameters={
                        "type": "OBJECT",
                        "properties": {
                            "project_id": {"type": "INTEGER"},
                            "mr_iid": {"type": "INTEGER"},
                            "comment": {"type": "STRING"}
                        },
                        "required": ["project_id", "mr_iid", "comment"]
                    }
                )
            )

            # --- Push / Commit Tools ---
            gemini_tools.append(
                types.FunctionDeclaration(
                    name="get_commit_diff",
                    description=(
                        "Fetches the code diff for a specific commit SHA in a project. "
                        "Use this for push events to review what changed in the latest commit."
                    ),
                    parameters={
                        "type": "OBJECT",
                        "properties": {
                            "project_id": {"type": "INTEGER"},
                            "commit_sha": {"type": "STRING"}
                        },
                        "required": ["project_id", "commit_sha"]
                    }
                )
            )
            gemini_tools.append(
                types.FunctionDeclaration(
                    name="post_commit_comment",
                    description=(
                        "Posts a review comment on a specific commit in GitLab. "
                        "Use this after reviewing a push event to leave feedback on the commit."
                    ),
                    parameters={
                        "type": "OBJECT",
                        "properties": {
                            "project_id": {"type": "INTEGER"},
                            "commit_sha": {"type": "STRING"},
                            "comment": {"type": "STRING"}
                        },
                        "required": ["project_id", "commit_sha", "comment"]
                    }
                )
            )

            tool_config = types.Tool(function_declarations=gemini_tools)

            # 4. Extract Git context and build the mission
            project_id = payload.get('project', {}).get('id') or payload.get('project_id')

            if event_type == "Merge Request Hook":
                mr_iid = payload.get('object_attributes', {}).get('iid')
                mission = (
                    f"A Merge Request (#{mr_iid}) was updated in Project {project_id}. "
                    f"Use the 'get_mr_diff' tool to read the code, and 'post_mr_comment' to leave your feedback."
                )

            elif event_type == "Push Hook":
                ref = payload.get('ref')
                branch = ref.split('/')[-1] if ref else "unknown"
                commits = payload.get('commits', [])

                # Use the latest (after) commit SHA for the diff
                after_sha = payload.get('after')

                if not after_sha or after_sha == "0000000000000000000000000000000000000000":
                    print("⚠️ Push event is a branch deletion. Ignoring.")
                    return

                commit_count = len(commits)
                commit_messages = "; ".join(
                    [f"'{c.get('message', '').strip()}'" for c in commits[:5]]
                )

                mission = (
                    f"{commit_count} commit(s) were pushed directly to branch '{branch}' "
                    f"in Project {project_id}. "
                    f"The latest commit SHA is {after_sha}. "
                    f"Commit messages: {commit_messages}. "
                    f"Use the 'get_commit_diff' tool to fetch and review the diff of the latest commit ({after_sha}), "
                    f"then use 'post_commit_comment' to leave a detailed code review comment on that commit. "
                    f"Highlight any bugs, bad practices, security issues, or suggestions for improvement."
                )

            elif event_type == "Note Hook":
                comment_body = payload.get('object_attributes', {}).get('note', '')
                mr_iid = payload.get('merge_request', {}).get('iid')

                if "@review-bot" in comment_body.lower():
                    mission = (
                        f"You were mentioned in a comment on Merge Request #{mr_iid} in Project {project_id}. "
                        f"The user said: '{comment_body}'. "
                        f"Use the 'get_mr_diff' tool to read the code context, "
                        f"and then reply to them using the 'post_mr_comment' tool."
                    )
                else:
                    print("💬 Comment does not mention @review-bot. Ignoring.")
                    return

            else:
                print(f"⚠️ Unhandled event '{event_type}'. Ignored.")
                return

            # 5. Initialize the Agent
            chat = client.chats.create(
                model=MODEL_ID,
                config=types.GenerateContentConfig(
                    tools=[tool_config],
                    temperature=0.2,
                    system_instruction=(
                        "You are an elite, autonomous DevOps Code Review Agent. "
                        "You have access to both MCP tools and custom Native tools. "
                        "Execute tools sequentially to complete your review mission. "
                        "When reviewing code, always comment on: correctness, security, "
                        "performance, readability, and best practices."
                    )
                )
            )

            print(f"🚀 Dispatched Mission: {mission}")

            # Initial Mission Dispatch (with retry)
            response = send_with_retry(chat, mission)

            if response.text:
                print(f"🤖 Agent says: {response.text}")

            # 6. The Autonomous Orchestration Loop
            while response.function_calls:
                for function_call in response.function_calls:
                    tool_name = function_call.name
                    tool_args = function_call.args

                    print(f"🛠️ Agent decided to use tool: {tool_name}")

                    # 🚦 ROUTER: Send the agent to the right tool system
                    if tool_name == "get_mr_diff":
                        url = f"https://gitlab.com/api/v4/projects/{tool_args['project_id']}/merge_requests/{tool_args['mr_iid']}/changes"
                        res = requests.get(url, headers={"PRIVATE-TOKEN": GITLAB_TOKEN})
                        if res.status_code == 200:
                            changes = res.json().get('changes', [])
                            result_text = "\n".join(
                                [f"File: {c['new_path']}\n{c['diff']}" for c in changes]
                            ) if changes else "No changes found."
                        else:
                            result_text = f"Failed to fetch MR diff. Status: {res.status_code}"

                    elif tool_name == "post_mr_comment":
                        url = f"https://gitlab.com/api/v4/projects/{tool_args['project_id']}/merge_requests/{tool_args['mr_iid']}/notes"
                        res = requests.post(
                            url,
                            headers={"PRIVATE-TOKEN": GITLAB_TOKEN},
                            json={"body": tool_args['comment']}
                        )
                        result_text = (
                            "✅ MR comment posted successfully!"
                            if res.status_code == 201
                            else f"❌ Failed to post MR comment. Error: {res.text}"
                        )

                    elif tool_name == "get_commit_diff":
                        # Fetches diff for a specific commit SHA
                        url = f"https://gitlab.com/api/v4/projects/{tool_args['project_id']}/repository/commits/{tool_args['commit_sha']}/diff"
                        res = requests.get(url, headers={"PRIVATE-TOKEN": GITLAB_TOKEN})
                        if res.status_code == 200:
                            diffs = res.json()
                            if diffs:
                                result_text = "\n\n".join(
                                    [f"File: {d['new_path']}\n{d['diff']}" for d in diffs]
                                )
                            else:
                                result_text = "No diff found for this commit."
                        else:
                            result_text = f"Failed to fetch commit diff. Status: {res.status_code}, Error: {res.text}"

                    elif tool_name == "post_commit_comment":
                        # Posts a comment on a specific commit
                        url = f"https://gitlab.com/api/v4/projects/{tool_args['project_id']}/repository/commits/{tool_args['commit_sha']}/comments"
                        res = requests.post(
                            url,
                            headers={"PRIVATE-TOKEN": GITLAB_TOKEN},
                            json={"note": tool_args['comment']}
                        )
                        result_text = (
                            "✅ Commit comment posted successfully!"
                            if res.status_code == 201
                            else f"❌ Failed to post commit comment. Error: {res.text}"
                        )

                    else:
                        # Fallback: MCP tool
                        mcp_result = await session.call_tool(tool_name, arguments=tool_args)
                        result_text = "\n".join(
                            [content.text for content in mcp_result.content if content.type == "text"]
                        )

                    print(f"📦 Tool result: {result_text[:200]}{'...' if len(result_text) > 200 else ''}")

                    # Send result back to Gemini for next planning step (with retry)
                    response = send_with_retry(
                        chat,
                        types.Part.from_function_response(
                            name=tool_name, response={"result": result_text}
                        )
                    )

                    if response.text:
                        print(f"🤖 Agent says: {response.text}")

            print("🎉 Mission complete!\n")


if __name__ == "__main__":
    # --- Test MR Hook ---
    # mock_payload = {
    #     "project": {"id": 12345678},
    #     "object_attributes": {"iid": 9}
    # }
    # asyncio.run(run_agent("Merge Request Hook", mock_payload))

    # --- Test Push Hook ---
    mock_payload = {
        "project": {"id": 12345678},          # Replace with your real Project ID
        "ref": "refs/heads/main",
        "before": "abc123",
        "after": "def456",                     # Replace with a real commit SHA
        "commits": [
            {"message": "Fix login bug"},
            {"message": "Add input validation"}
        ]
    }
    print("🧪 Running local push event agent test...")
    asyncio.run(run_agent("Push Hook", mock_payload))