import requests
import os

GITLAB_TOKEN = os.environ.get("GITLAB_TOKEN")  # your GitLab personal access token
GITLAB_URL = "https://gitlab.com/api/v4"

headers = {
    "PRIVATE-TOKEN": GITLAB_TOKEN
}


def get_diff(project_id, mr_iid):
    """Fetch the code diff for a merge request."""
    url = f"{GITLAB_URL}/projects/{project_id}/merge_requests/{mr_iid}/diffs"
    response = requests.get(url, headers=headers)

    if response.status_code != 200:
        print(f"Error fetching diff: {response.status_code} {response.text}")
        return None

    diffs = response.json()

    # Combine all file diffs into one string
    combined_diff = ""
    for diff in diffs:
        file_path = diff.get('new_path', 'unknown')
        diff_text = diff.get('diff', '')
        combined_diff += f"\n\n--- File: {file_path} ---\n{diff_text}"

    return combined_diff.strip()


def post_comment(project_id, mr_iid, file_path, line, comment):
    """Post a comment on a specific line of a merge request."""
    url = f"{GITLAB_URL}/projects/{project_id}/merge_requests/{mr_iid}/discussions"

    data = {
        "body": comment,
        "position": {
            "position_type": "text",
            "base_sha": get_mr_sha(project_id, mr_iid, 'base'),
            "start_sha": get_mr_sha(project_id, mr_iid, 'start'),
            "head_sha": get_mr_sha(project_id, mr_iid, 'head'),
            "new_path": file_path,
            "new_line": line
        }
    }

    response = requests.post(url, json=data, headers=headers)
    if response.status_code not in [200, 201]:
        # If line comment fails, post as general comment instead
        post_general_comment(project_id, mr_iid, f"**{file_path} (line {line}):** {comment}")


def post_general_comment(project_id, mr_iid, comment):
    """Post a general comment on the merge request (not line specific)."""
    url = f"{GITLAB_URL}/projects/{project_id}/merge_requests/{mr_iid}/notes"
    data = {"body": comment}
    requests.post(url, json=data, headers=headers)


def post_summary(project_id, mr_iid, issues):
    """Post an overall review summary comment."""
    if not issues:
        summary = "## 🤖 AI Code Review\n\n✅ **Looks good!** No issues found. Great work!"
    else:
        critical = [i for i in issues if i['severity'] == 'critical']
        warnings = [i for i in issues if i['severity'] == 'warning']
        suggestions = [i for i in issues if i['severity'] == 'suggestion']

        summary = f"## 🤖 AI Code Review Summary\n\n"
        summary += f"Found **{len(issues)} issue(s)** in this PR:\n\n"

        if critical:
            summary += f"🚨 **Critical:** {len(critical)} issue(s) — must fix before merge\n"
        if warnings:
            summary += f"⚠️ **Warnings:** {len(warnings)} issue(s) — should fix\n"
        if suggestions:
            summary += f"💡 **Suggestions:** {len(suggestions)} — optional improvements\n"

        summary += "\n---\n*Review each inline comment above for details and fixes.*"

    post_general_comment(project_id, mr_iid, summary)


def get_mr_sha(project_id, mr_iid, sha_type):
    """Get the SHA values needed for line comments."""
    url = f"{GITLAB_URL}/projects/{project_id}/merge_requests/{mr_iid}/versions"
    response = requests.get(url, headers=headers)

    if response.status_code != 200:
        return None

    versions = response.json()
    if not versions:
        return None

    latest = versions[0]
    if sha_type == 'head':
        return latest.get('head_commit_sha')
    elif sha_type == 'base':
        return latest.get('base_commit_sha')
    elif sha_type == 'start':
        return latest.get('start_commit_sha')