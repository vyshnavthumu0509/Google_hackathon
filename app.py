from flask import Flask, request
import threading
import Gitlab_helper
import Reviewers

app = Flask(__name__)

def process_review_in_background(project_id, mr_iid):
    """This runs in the background so we don't keep GitLab waiting!"""
    print(f"\n--- 🚀 Background processing started for PR #{mr_iid} ---")

    print("Fetching code diff from GitLab...")
    diff_text = Gitlab_helper.get_diff(project_id, mr_iid)
    print(f"🔍 DEBUG - Diff Length: {len(diff_text)} characters") # Add this line
    print(f"🔍 DEBUG - Diff Content:\n{diff_text}") # Add this line
    
    if not diff_text:
        print("❌ No diff found or error fetching diff.")
        return

    print("🧠 Sending code to Gemini for analysis...")
    issues = Reviewers.analyse_code(diff_text)

    if issues:
        print(f"🎯 Found {len(issues)} issues! Posting inline comments...")
        for item in issues:
            file_path = item.get('file')
            line = item.get('line')
            severity = item.get('severity', 'warning').upper()
            issue_desc = item.get('issue')
            fix = item.get('fix')
            
            comment_body = f"**[{severity}]** {issue_desc}\n\n💡 *Fix suggestion:* {fix}"
            Gitlab_helper.post_comment(project_id, mr_iid, file_path, line, comment_body)
    else:
        print("✅ Code looks clean, no issues found.")

    print("📝 Posting final summary...")
    Gitlab_helper.post_summary(project_id, mr_iid, issues)
    print(f"✅ Review complete for PR #{mr_iid}!\n")


@app.route('/')
def home():
    return "Agent is running!"

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json

    # Ignore if not a merge request
    if data.get('object_kind') != 'merge_request':
        return "ok"

    project_id = data['project']['id']
    mr_iid = data['object_attributes']['iid']

    # 🚀 Start the background thread
    thread = threading.Thread(target=process_review_in_background, args=(project_id, mr_iid))
    thread.start()

    # ⚡ Immediately tell GitLab "We received it!" to prevent the Timeout Error
    return "Processing started", 200

if __name__ == '__main__':
    app.run(port=5000, debug=True)