import os
from dotenv import load_dotenv
from src.jira_client import JiraClient
from rich.console import Console
from rich.progress import track

load_dotenv()
console = Console()

def purge_jira():
    client = JiraClient()
    project = os.getenv("JIRA_PROJECT_KEY", "SRE")
    
    if not client.auth_header:
        console.print("[bold red]Error:[/] Jira credentials missing in .env")
        return

    console.print(f"[bold cyan]Maintenance:[/] Scanning project [bold]{project}[/] for all issues...")

    # 1. Search using the JQL proven to work
    jql = f"project = '{project}'"
    res = client.search_issues(jql, max_results=100)
    
    if "error" in res:
        console.print(f"[bold red]Search Failed:[/] {res}")
        return

    issues = res.get("issues", [])
    if not issues:
        console.print("[green]No issues found to delete.[/green]")
        return

    console.print(f"Found [bold]{len(issues)}[/bold] issues. Starting purge...")

    # 2. Delete Loop
    deleted_count = 0
    for issue in track(issues, description="Deleting tickets..."):
        key = issue["key"]
        del_res = client.delete_issue(key)
        if "error" not in del_res:
            deleted_count += 1
        else:
            console.print(f"[red]Failed to delete {key}: {del_res}[/red]")

    console.print(f"\n[bold green]Cleanup Complete![/bold green] Removed {deleted_count} issues from {project}.")

if __name__ == "__main__":
    purge_jira()
