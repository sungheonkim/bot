import os
import re
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from jira import JIRA
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

JIRA_SERVER = os.getenv("JIRA_SERVER")
JIRA_EMAIL = os.getenv("JIRA_EMAIL")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN")
PROJECT_KEY = os.getenv("PROJECT_KEY", "").split(",")[0].strip()

def get_jira():
    return JIRA(server=JIRA_SERVER, basic_auth=(JIRA_EMAIL, JIRA_API_TOKEN))

class JiraRequest(BaseModel):
    content: str
    project_key: str

@app.post("/api/create-issues")
async def create_issues(req: JiraRequest):
    try:
        if not os.getenv("JIRA_SERVER") or not os.getenv("JIRA_EMAIL") or not os.getenv("JIRA_API_TOKEN"):
            return {"status": "error", "message": "Jira Environment Variables are missing."}
            
        jira = get_jira()
        available_types = {it.name: it.name for it in jira.issue_types()}
        
        def get_type_name(pref):
            for name in available_types:
                if pref.lower() in name.lower():
                    return name
            return pref

        epic_type = get_type_name("Epic")
        story_type = get_type_name("Story")

        lines = req.content.strip().split("\n")
        current_epic_issue = None
        created_issues = []

        for line in lines:
            line = line.strip()
            if not line: continue
            
            # 1. Epic Header
            if line.startswith("### **Epic"):
                summary = line.replace("### **", "").replace("**", "")
                fields = {
                    'project': req.project_key,
                    'summary': summary,
                    'description': "Created via JiraBot Web UI",
                    'issuetype': {'name': epic_type}
                }
                epic_issue = jira.create_issue(fields=fields)
                current_epic_issue = epic_issue
                created_issues.append({"key": epic_issue.key, "summary": summary, "type": "Epic"})
                continue
            
            # 2. Story Header
            if line.startswith("**스토리") or line.startswith("스토리"):
                # 헤더(스토리 타이틀)는 지라 이슈로 올리지 않고 패스합니다. (유저 피드백)
                continue
                
            # 3. Bullet points
            match = re.match(r"- \[(.*?)\] (.*)", line)
            if match:
                tags_str = match.group(1)
                task_content = match.group(2)
                tags = [t.strip() for t in tags_str.split("/")]
                
                for tag in tags:
                    summary = f"[{tag}] {task_content}"
                    fields = {
                        'project': req.project_key,
                        'summary': summary,
                        'issuetype': {'name': story_type}
                    }
                    if current_epic_issue:
                        fields['parent'] = {'id': current_epic_issue.id}
                    
                    issue = jira.create_issue(fields=fields)
                    created_issues.append({"key": issue.key, "summary": summary, "type": "Story"})

        return {"status": "success", "created": created_issues}
    except Exception as e:
        return {"status": "error", "message": f"Server Error: {str(e)}"}

@app.get("/", response_class=HTMLResponse)
async def get_index():
    file_path = os.path.join(os.path.dirname(__file__), "index.html")
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
