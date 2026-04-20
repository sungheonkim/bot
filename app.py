import os
import re
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import gitlab
import google.generativeai as genai
from jira import JIRA
from dotenv import load_dotenv
import asyncio

load_dotenv()

app = FastAPI()
review_lock = None

JIRA_SERVER = os.getenv("JIRA_SERVER")
JIRA_EMAIL = os.getenv("JIRA_EMAIL")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN")
PROJECT_KEY = os.getenv("PROJECT_KEY", "").split(",")[0].strip()

import google.generativeai as genai
if os.getenv("GEMINI_API_KEY"):
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

KV_REST_API_URL = os.getenv("KV_REST_API_URL")
KV_REST_API_TOKEN = os.getenv("KV_REST_API_TOKEN")

# 서버리스 환경 임시 로그 저장 (최대 50개 유지)
webhook_logs = []

def add_log(msg: str):
    import datetime, json, urllib.request
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted_msg = f"[{now}] {msg}"
    
    webhook_logs.insert(0, formatted_msg)
    if len(webhook_logs) > 50:
        webhook_logs.pop()

    # Vercel KV (Redis) 에 영구 보존
    if KV_REST_API_URL and KV_REST_API_TOKEN:
        try:
            req = urllib.request.Request(
                f"{KV_REST_API_URL}", 
                data=json.dumps(["LPUSH", "bot_logs", formatted_msg]).encode("utf-8"),
                headers={"Authorization": f"Bearer {KV_REST_API_TOKEN}", "Content-Type": "application/json"}
            )
            urllib.request.urlopen(req, timeout=3)
            
            # 최신 100개만 유지
            req_trim = urllib.request.Request(
                f"{KV_REST_API_URL}", 
                data=json.dumps(["LTRIM", "bot_logs", "0", "99"]).encode("utf-8"),
                headers={"Authorization": f"Bearer {KV_REST_API_TOKEN}", "Content-Type": "application/json"}
            )
            urllib.request.urlopen(req_trim, timeout=3)
        except Exception as e:
            print("KV 저장 실패:", e)

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
                    add_log(f"✅ 지라 스토리 생성 완료: {issue.key}")

        add_log("🎉 지라 일괄 생성 작업이 완료되었습니다!")
        return {"status": "success", "created": created_issues}
    except Exception as e:
        add_log(f"❌ 지라 이슈 생성 에러: {str(e)}")
        return {"status": "error", "message": f"Server Error: {str(e)}"}

@app.post("/api/webhook/gitlab")
async def gitlab_webhook(request: Request):
    try:
        payload = await request.json()
        
        # MR 생성/업데이트 이벤트인지 확인
        action = payload.get("object_attributes", {}).get("action")
        object_kind = payload.get("object_kind")
        
        is_open = (action == "open")
        # GitLab에서 코드가 푸시되어 업데이트되는 경우 oldrev가 페이로드에 포함됨
        is_code_update = (action == "update" and "oldrev" in payload.get("object_attributes", {}))
        
        if object_kind == "merge_request" and (is_open or is_code_update):
            global review_lock
            if review_lock is None:
                review_lock = asyncio.Lock()
                
            async with review_lock:
                project_id = payload["project"]["id"]
                mr_iid = payload["object_attributes"]["iid"]
                mr_title = payload["object_attributes"].get("title", "제목 없음")
                mr_desc = payload["object_attributes"].get("description", "내용 없음")
                
                add_log(f"GitLab Webhook 수신: MR #{mr_iid} '{mr_title}' 분석 시작...")
                
                GITLAB_URL = os.getenv("GITLAB_URL", "https://lab.ssafy.com")
                GITLAB_TOKEN = os.getenv("GITLAB_TOKEN")
                
                if not GITLAB_TOKEN or not os.getenv("GEMINI_API_KEY"):
                    add_log("오류: GitLab 토큰 또는 Gemini API 키가 없습니다.")
                    return {"status": "error", "message": "Missing GitLab or Gemini tokens"}
                    
                gl = gitlab.Gitlab(GITLAB_URL, private_token=GITLAB_TOKEN)
                gl.auth()
                
                project = gl.projects.get(project_id)
                mr = project.mergerequests.get(mr_iid)
                
                # 변경점 수집
                changes = mr.changes()
                diff_text = ""
                for change in changes.get('changes', []):
                    diff_text += f"---\nFile: {change['new_path']}\n"
                    diff_text += f"{change['diff']}\n\n"
                    
                if diff_text:
                    add_log(f"MR #{mr_iid} Diff 추출 완료. Gemini AI에게 코드 리뷰 요청 중...")
                    # Gemini에게 리뷰 요청
                    model = genai.GenerativeModel('gemini-3.1-flash-lite')
                    prompt = f"""다음은 GitLab Merge Request 정보와 코드 변경 사항이야. 코드 뿐만 아니라 MR 제목과 내용이 적절한지도 함께 리뷰해줘. 버그가 있거나 개선할 점은 마크다운으로 예쁘게 포맷팅해서 작성해.

[MR 제목]: {mr_title}
[MR 내용]: {mr_desc}

[코드 변경사항]:
{diff_text[:8000]}"""
                    response = await model.generate_content_async(prompt)
                    
                    # MR 코멘트로 결과 작성
                    mr.notes.create({'body': f"🤖 **AI 통합 리뷰 봇** (자동 분석)\n\n{response.text}"})
                    add_log(f"✅ MR #{mr_iid} 리뷰 코멘트 작성 완료!")
                else:
                    add_log(f"⚠️ MR #{mr_iid} 에 분석할 코드 변경사항이 없습니다.")

        return {"status": "success"}
    except Exception as e:
        add_log(f"❌ Webhook 에러 발생: {str(e)}")
        print("Webhook Error:", e)
        return {"status": "error"}

@app.get("/api/logs")
async def get_logs():
    if KV_REST_API_URL and KV_REST_API_TOKEN:
        import json, urllib.request
        try:
            req = urllib.request.Request(
                f"{KV_REST_API_URL}", 
                data=json.dumps(["LRANGE", "bot_logs", "0", "49"]).encode("utf-8"),
                headers={"Authorization": f"Bearer {KV_REST_API_TOKEN}", "Content-Type": "application/json"}
            )
            response = urllib.request.urlopen(req, timeout=5)
            data = json.loads(response.read().decode("utf-8"))
            if "result" in data and isinstance(data["result"], list):
                return {"status": "success", "logs": data["result"]}
        except Exception as e:
            print("KV 읽기 실패:", e)
            
    return {"status": "success", "logs": webhook_logs}

@app.get("/", response_class=HTMLResponse)
async def get_index():
    file_path = os.path.join(os.path.dirname(__file__), "index.html")
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
