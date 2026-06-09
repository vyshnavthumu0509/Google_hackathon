import os
import asyncio
from google import genai
from google.genai import types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

client = genai.Client(api_key="AIzaSy...") 
MODEL_ID = "gemini-3.0-flash" 

server_params = StdioServerParameters(
    command="npx",
    args=["-y", "@modelcontextprotocol/server-gitlab"],
    env={
        "GITLAB_PERSONAL_ACCESS_TOKEN": "glpat-...", 
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

            # Convert MCP tools for Gemini (and sanitize them)
            gemini_tools = []
            for tool in mcp_tools.tools:
                # Grab the schema provided by the MCP server
                clean_schema = tool.inputSchema.copy() if tool.inputSchema else {}
                
                # Google's strict SDK hates the "$schema" key, so we delete it if it exists
                clean_schema.pop("$schema", None)
                
                gemini_tools.append(
                    types.FunctionDeclaration(
                        name=tool.name, 
                        description=tool.description, 
                        parameters=clean_schema
                    )
                )
                
            tool_config = types.Tool(function_declarations=gemini_tools)
            
            # Extract specific details based on what happened
            project_id = payload.get('project', {}).get('id') or payload.get('project_id')
            
            # Formulate a custom mission instruction for Gemini based on the Git event
            if event_type == "Merge Request Hook":
                mr_iid = payload.get('object_attributes', {}).get('iid')
                mission = f"A Merge Request (#{mr_iid}) was updated in Project {project_id}. Use your tools to check the diff and leave review comments."
                
            elif event_type == "Push Hook":
                ref = payload.get('ref') # e.g., refs/heads/main
                branch = ref.split('/')[-1] if ref else "unknown"
                before_sha = payload.get('before')
                after_sha = payload.get('after')
                mission = f"Code was pushed directly to branch '{branch}' in Project {project_id} (from commit {before_sha} to {after_sha}). Use your tools to check the changes for any critical breaking bugs or security flaws."

            elif event_type == "Note Hook": # Someone commented on a line or thread
                comment_body = payload.get('object_attributes', {}).get('note')
                discussion_id = payload.get('object_attributes', {}).get('discussion_id')
                
                # Check if someone is talking directly to the agent
                if "@review-bot" in comment_body.lower():
                    mission = f"A user mentioned you in a comment discussion ({discussion_id}) in Project {project_id}. The comment says: '{comment_body}'. Use your tools to reply to their question contextually."
                else:
                    print("💬 Comment does not mention @review-bot. Ignoring.")
                    return
            else:
                mission = f"An unhandled event '{event_type}' occurred in Project {project_id}. Review the payload and check if any system intervention is needed: {str(payload)[:500]}"

            # Start the agent chat session
            chat = client.chats.create(
                model=MODEL_ID,
                config=types.GenerateContentConfig(
                    tools=[tool_config],
                    temperature=0.2,
                    system_instruction=(
                        "You are an elite, autonomous DevOps Platform Agent. You handle multi-turn operations "
                        "on GitLab using your provided MCP tools. Execute tools sequentially until your task is fully completed."
                    )
                )
            )
            
            print(f"🚀 Dispatched Mission: {mission}")
            response = chat.send_message(mission)

            # The Autonomous Tool Execution Loop
            while response.function_calls:
                for function_call in response.function_calls:
                    tool_name = function_call.name
                    tool_args = function_call.args

                    print(f"🛠️ Agent invoking tool: {tool_name}")
                    mcp_result = await session.call_tool(tool_name, arguments=tool_args)
                    result_text = "\n".join([content.text for content in mcp_result.content if content.type == "text"])
                    
                    response = chat.send_message(
                        types.Part.from_function_response(
                            name=tool_name, response={"result": result_text}
                        )
                    )
            
            print("🎉 Mission complete!")
            
            
if __name__ == "__main__":
    # Fake a Merge Request payload to test the agent's brain directly
    mock_payload = {
        "project": {"id": 12345678},  # Replace with your actual GitLab Project ID
        "object_attributes": {
            "iid": 9                  # Replace with your actual Merge Request IID
        }
    }
    print("🧪 Running local agent test...")
    asyncio.run(run_agent("Merge Request Hook", mock_payload))