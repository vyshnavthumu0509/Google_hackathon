import os
import time
import asyncio
import requests
from google import genai
from google.genai import types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# 1. Setup the Modern Gemini Client
# TODO: Insert your actual keys here!
GEMINI_API_KEY = "AQ.Ab8RN6JgQMAFBs-btKfJz4O5h3IbigYJP4RriAW58_8OGX-ClQ" 
GITLAB_TOKEN = "glpat-aSG58mfgC9mbBuTCO2NVYGM6MQpvOjEKdTpuN2oycQ8.01.170g8a9q9"

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

            # 🚀 THE HYBRID UPGRADE: Inject Custom Python Tools for Code Review
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
            
            tool_config = types.Tool(function_declarations=gemini_tools)

            # 4. Extract Git context to build the mission
            project_id = payload.get('project', {}).get('id') or payload.get('project_id')
            
            if event_type == "Merge Request Hook":
                mr_iid = payload.get('object_attributes', {}).get('iid')
                mission = f"A Merge Request (#{mr_iid}) was updated in Project {project_id}. Use the 'get_mr_diff' tool to read the code, and 'post_mr_comment' to leave your feedback."
                
            elif event_type == "Push Hook":
                ref = payload.get('ref')
                branch = ref.split('/')[-1] if ref else "unknown"
                before_sha = payload.get('before')
                after_sha = payload.get('after')
                mission = f"Code was pushed directly to branch '{branch}' in Project {project_id} (from commit {before_sha} to {after_sha}). Use your MCP tools to check the commit changes and summarize any potential issues."

            elif event_type == "Note Hook":
                comment_body = payload.get('object_attributes', {}).get('note', '')
                # Note hooks store the MR ID in a slightly different place in the JSON payload
                mr_iid = payload.get('merge_request', {}).get('iid') 
                
                if "@review-bot" in comment_body.lower():
                    mission = f"You were mentioned in a comment on Merge Request #{mr_iid} in Project {project_id}. The user said: '{comment_body}'. Use the 'get_mr_diff' tool to read the code context, and then reply to them using the 'post_mr_comment' tool."
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
                        "Execute tools sequentially to complete your review mission."
                    )
                )
            )
            
            print(f"🚀 Dispatched Mission: {mission}")
            
            # Initial Mission Dispatch
            max_retries = 100
            for attempt in range(max_retries):
                try:
                    response = chat.send_message(mission)
                    break
                except Exception as e:
                    if "503" in str(e) or "429" in str(e):
                        print(f"⚠️ API is busy. Waiting 10 seconds...")
                        time.sleep(10)
                    else:
                        raise e
            
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
                            result_text = "\n".join([f"File: {c['new_path']}\n{c['diff']}" for c in changes]) if changes else "No changes."
                        else:
                            result_text = "Failed to fetch diff."

                    elif tool_name == "post_mr_comment":
                        url = f"https://gitlab.com/api/v4/projects/{tool_args['project_id']}/merge_requests/{tool_args['mr_iid']}/notes"
                        res = requests.post(url, headers={"PRIVATE-TOKEN": GITLAB_TOKEN}, json={"body": tool_args['comment']})
                        result_text = "Comment posted successfully!" if res.status_code == 201 else f"Failed to post. Error: {res.text}"

                    else:
                        # If it's not a custom tool, it must be an MCP tool!
                        mcp_result = await session.call_tool(tool_name, arguments=tool_args)
                        result_text = "\n".join([content.text for content in mcp_result.content if content.type == "text"])
                    
                    # Send the result back to Gemini so it can plan its next move
                    response = chat.send_message(
                        types.Part.from_function_response(
                            name=tool_name, response={"result": result_text}
                        )
                    )
                    
                    if response.text:
                        print(f"🤖 Agent says: {response.text}")
            
            print("🎉 Mission complete!\n")

if __name__ == "__main__":
    # Local Testing Block
    mock_payload = {
        "project": {"id": 12345678}, # Replace with your real Project ID!
        "object_attributes": {"iid": 9} # Replace with your real MR ID!
    }
    print("🧪 Running local agent test...")
    asyncio.run(run_agent("Merge Request Hook", mock_payload))