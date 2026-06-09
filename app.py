from flask import Flask, request, jsonify
import threading
import asyncio
import Reviewers

app = Flask(__name__)

def run_async_agent(event_type, payload):
    """Background worker to handle the agent execution asynchronously"""
    # Create a new event loop for this background thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # Pass the event type and raw payload to the agent
    loop.run_until_complete(Reviewers.run_agent(event_type, payload))
    loop.close()

@app.route('/webhook', methods=['POST'])
def webhook():
    # Detect what kind of action happened in GitLab
    event_type = request.headers.get('X-Gitlab-Event')
    payload = request.json

    if not event_type:
        return jsonify({"status": "ignored", "reason": "Missing event header"}), 400

    print(f"🚀 Received GitLab Event: {event_type}")

    # Immediately hand off to a background thread to prevent GitLab timeouts (200 OK)
    threading.Thread(target=run_async_agent, args=(event_type, payload)).start()

    return jsonify({"status": "accepted", "message": "Agent processing started"}), 200

if __name__ == '__main__':
    app.run(debug=True, port=5000)