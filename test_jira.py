import os
from dotenv import load_dotenv
from src.jira_client import JiraClient
from rich.console import Console

load_dotenv()
console = Console()

def test_jira():
    console.print("[bold cyan]Jira Connection Test[/bold cyan]")
    client = JiraClient()
    
    if not client.auth_header:
        console.print("[bold red]Error:[/] Credentials missing in .env")
        return

    # 1. Create Test Issue
    console.print("\n[bold yellow]1. Attempting to create issue...[/bold yellow]")
    summary = "Jira Integration Test - Reliability Agent"
    description = "This is a test issue to verify API connectivity."
    res = client.create_issue(summary, description)
    
    if "error" in res:
        console.print(f"[bold red]Failed to create issue:[/bold red] {res}")
        return
    
    key = res.get("key")
    console.print(f"[bold green]Success![/bold green] Created issue: [bold]{key}[/bold]")

    # 2. Add Comment
    console.print(f"\n[bold yellow]2. Adding comment to {key}...[/bold yellow]")
    com_res = client.add_comment(key, "Automated test comment from Reliability Agent.")
    if "error" in com_res:
        console.print(f"[bold red]Failed to add comment:[/bold red] {com_res}")
    else:
        console.print("[bold green]Success![/bold green] Comment added.")

    # 3. Resolve Issue
    console.print(f"\n[bold yellow]3. Attempting to resolve {key}...[/bold yellow]")
    res_res = client.resolve_issue(key)
    if "error" in res_res:
        console.print(f"[bold red]Failed to resolve issue:[/bold red] {res_res}")
        console.print("[dim]Note: Transition IDs for 'Done' vary by Jira project template.[/dim]")
    else:
        console.print(f"[bold green]Success![/bold green] Issue {key} resolved.")

if __name__ == "__main__":
    test_jira()
