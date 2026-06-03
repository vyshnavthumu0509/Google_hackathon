from flask import Flask, request
import requests
import os
import json
import re
import google.generativeai as genai

app = Flask(__name__)

GITLAB_TOKEN = os.environ.get("GITLAB_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

headers = {"PRIVATE-TOKEN": GITLAB_TOKEN}
GITLAB_URL = "https://gitlab.com/api/v4"

@app.route('/')
def home():
    return "Agent is running!"

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json

    # ignore if not a merge request
    if data.get('object_kind') != 'merge_request':
        return "ok"

    project_id = data['project']['id']
    mr_iid = data['object_attributes']['iid']

    # get the diff
    url = f"{GITLAB_URL}/projects/{project_id}/merge_requests/{mr_iid}/diffs"
    diff_response = requests.get(url, headers=headers).json()
    
    diff_text = ""
    for d in diff_response:
        diff_text += f"\nFile: {d['new_path']}\n{d['diff']}"

    # send to gemini
    prompt = f"Review this code diff and list any bugs or issues in simple bullet points:\n{diff_text}"
    result = model.generate_content(prompt)
    review = result.text

    # post comment on the MR
    comment_url = f"{GITLAB_URL}/projects/{project_id}/merge_requests/{mr_iid}/notes"
    requests.post(comment_url, headers=headers, json={"body": f"## 🤖 AI Review\n{review}"})

    return "done"

if __name__ == '__main__':
    app.run(port=5000, debug=True)