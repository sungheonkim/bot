import os
import sys
from jira import JIRA
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import print as rprint
import argparse

# .env 파일 로드
load_dotenv()

# 상수 설정
JIRA_SERVER = os.getenv("JIRA_SERVER")
JIRA_EMAIL = os.getenv("JIRA_EMAIL")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN")
DEFAULT_PROJECT_KEYS = os.getenv("PROJECT_KEY", "")

console = Console()

def get_jira_client():
    """지라 클라이언트 초기화 및 인증"""
    if not all([JIRA_SERVER, JIRA_EMAIL, JIRA_API_TOKEN]):
        console.print("[bold red]❌ 에러:[/bold red] API 설정이 누락되었습니다. .env 파일을 확인해주세요.")
        sys.exit(1)
    
    try:
        return JIRA(server=JIRA_SERVER, basic_auth=(JIRA_EMAIL, JIRA_API_TOKEN))
    except Exception as e:
        console.print(f"[bold red]❌ 연결 실패:[/bold red] {str(e)}")
        sys.exit(1)

def build_project_jql(project_keys):
    """프로젝트 키 목록을 JQL 필터 문자열로 변환"""
    if not project_keys or project_keys.upper() == "ALL":
        return ""
    
    keys = [k.strip() for k in project_keys.split(",") if k.strip()]
    if not keys:
        return ""
    
    if len(keys) == 1:
        return f'project = "{keys[0]}" AND '
    else:
        keys_str = ", ".join([f'"{k}"' for k in keys])
        return f'project IN ({keys_str}) AND '

def show_assigned_issues(jira, project_keys):
    """나에게 할당된 이슈 목록 출력"""
    project_filter = build_project_jql(project_keys)
    jql = f'{project_filter}assignee = currentUser() AND resolution = Unresolved'
    issues = jira.search_issues(jql)

    title_proj = project_keys if project_keys else "All Projects"
    table = Table(title=f"📋 [bold cyan]{title_proj}[/bold cyan] 할당 업무 목록", show_header=True, header_style="bold magenta")
    table.add_column("Project", style="dim")
    table.add_column("Key", style="bold", no_wrap=True)
    table.add_column("Summary", style="white")
    table.add_column("Type", justify="center")
    table.add_column("Status", justify="center")

    if not issues:
        console.print(Panel(Text("🎉 할당된 업무가 없습니다! 나이스!", justify="center", style="bold green")))
        return

    for issue in issues:
        status = issue.fields.status.name
        status_style = "green" if status in ["Done", "Resolved", "완료"] else "yellow" if status in ["In Progress", "진행 중"] else "cyan"
        
        table.add_row(
            issue.fields.project.key,
            issue.key,
            issue.fields.summary,
            issue.fields.issuetype.name,
            f"[{status_style}]{status}[/{status_style}]"
        )
    
    console.print(table)

def generate_scrum_report(jira, project_keys):
    """데일리 스크럼용 리포트 생성"""
    project_filter = build_project_jql(project_keys)
    
    done_jql = f'{project_filter}assignee = currentUser() AND status in ("Done", "Resolved", "완료") AND updated >= -1d'
    todo_jql = f'{project_filter}assignee = currentUser() AND status not in ("Done", "Resolved", "완료")'

    done_issues = jira.search_issues(done_jql)
    todo_issues = jira.search_issues(todo_jql)

    report = Text()
    report.append("\n🚀 데일리 스크럼 리포트\n", style="bold reverse blue")
    
    report.append("\n✅ [bold green]Yesterday (Done):[/bold green]\n")
    if not done_issues:
        report.append("  - (최근 완료한 업무가 없습니다)\n", style="dim")
    for issue in done_issues:
        report.append(f"  - [{issue.fields.project.key}] {issue.key}: {issue.fields.summary}\n")

    report.append("\n🏃 [bold yellow]Today (In Progress/To Do):[/bold yellow]\n")
    if not todo_issues:
        report.append("  - (진행 중인 업무가 없습니다)\n", style="dim")
    for issue in todo_issues:
        report.append(f"  - [{issue.fields.project.key}] {issue.key}: {issue.fields.summary}\n")

    console.print(Panel(report, border_style="blue"))

def update_issue_status(jira, issue_key, target_status):
    """이슈 상태 변경"""
    try:
        issue = jira.issue(issue_key)
        transitions = jira.transitions(issue)
        target = target_status.lower()
        transition_id = None
        transition_name = ""

        alias = {
            "done": ["완료", "done", "resolved", "fixed", "close"],
            "in progress": ["진행 중", "in progress", "started", "working"],
            "to do": ["해야 할 일", "to do", "open", "backlog", "reopen"]
        }
        target_names = alias.get(target, [target])

        for t in transitions:
            t_name = t['name'].lower()
            t_to_status = t['to']['name'].lower()
            if any(name in t_name or name in t_to_status for name in target_names):
                transition_id = t['id']
                transition_name = t['name']
                break
        
        if transition_id:
            jira.transition_issue(issue, transition_id)
            console.print(Panel(Text(f"✅ [{issue_key}] 상태가 '{transition_name}'(으)로 변경되었습니다.", style="bold green")))
        else:
            available = ", ".join([t['name'] for t in transitions])
            console.print(Panel(Text(f"❌ '{target_status}' 상태로 변경할 수 없습니다.\n가능한 전이: {available}", style="bold red")))
    except Exception as e:
        console.print(f"[bold red]❌ 이슈 업데이트 실패:[/bold red] {str(e)}")

def create_issue(jira, project, summary, description=None, issue_type="Story", assignee=None):
    """새 이슈 생성"""
    try:
        issue_dict = {
            'project': project,
            'summary': summary,
            'issuetype': {'name': issue_type},
        }
        if description:
            issue_dict['description'] = description
        
        new_issue = jira.create_issue(fields=issue_dict)
        
        # 담당자 할당 if provided
        if assignee:
            jira.assign_issue(new_issue, assignee)

        console.print(Panel(
            Text.assemble(
                ("✅ 새 이슈가 생성되었습니다!\n\n", "bold green"),
                ("프로젝트: ", "white"), (f"{project}\n", "bold cyan"),
                ("티켓 번호: ", "white"), (f"{new_issue.key}\n", "bold cyan"),
                ("요약: ", "white"), (f"{summary}\n", "italic"),
                ("담당자: ", "white"), (f"{assignee if assignee else '미지정'}\n", "bold yellow"),
                ("링크: ", "white"), (f"{new_issue.permalink()}", "blue underline")
            ),
            title="이슈 생성 성공", border_style="green"
        ))
        return new_issue
    except Exception as e:
        console.print(f"[bold red]❌ 이슈 생성 실패:[/bold red] {str(e)}")
        return None

def main():
    parser = argparse.ArgumentParser(description="🚀 Jira Task Manager Pro CLI")
    subparsers = parser.add_subparsers(dest="command", help="명령어")

    # list
    list_p = subparsers.add_parser("list", help="이슈 조회")
    list_p.add_argument("project", nargs="?", default=DEFAULT_PROJECT_KEYS)

    # report
    report_p = subparsers.add_parser("report", help="리포트 생성")
    report_p.add_argument("project", nargs="?", default=DEFAULT_PROJECT_KEYS)

    # create
    create_p = subparsers.add_parser("create", help="이슈 생성")
    create_p.add_argument("project", help="프로젝트 키")
    create_p.add_argument("summary", help="제목")
    create_p.add_argument("--desc", "--description", help="상세 설명")
    create_p.add_argument("--type", default="Story", help="이슈 유형 (Story, Bug, Task 등)")
    create_p.add_argument("--assignee", help="담당자 이메일 또는 ID")

    # start, done, todo
    for cmd in ["start", "done", "todo"]:
        cmd_p = subparsers.add_parser(cmd, help=f"상태 변경: {cmd}")
        cmd_p.add_argument("key")

    # move
    move_p = subparsers.add_parser("move", help="커스텀 상태 변경")
    move_p.add_argument("key")
    move_p.add_argument("status")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    jira = get_jira_client()
    if args.command == "list": show_assigned_issues(jira, args.project)
    elif args.command == "report": generate_scrum_report(jira, args.project)
    elif args.command == "create": create_issue(jira, args.project, args.summary, args.desc, args.type, args.assignee)
    elif args.command == "start": update_issue_status(jira, args.key, "in progress")
    elif args.command == "done": update_issue_status(jira, args.key, "done")
    elif args.command == "todo": update_issue_status(jira, args.key, "to do")
    elif args.command == "move": update_issue_status(jira, args.key, args.status)

if __name__ == "__main__":
    main()
