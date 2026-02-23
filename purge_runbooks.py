import os
import json
import sys
import urllib.request
import urllib.error
from dotenv import load_dotenv
from rich.console import Console

load_dotenv()
console = Console()

def purge():
    es_url = os.getenv("ELASTIC_URL", "").rstrip("/")
    api_key = os.getenv("ELASTIC_API_KEY", "")
    index = "runbooks-demo"
    
    if not es_url or not api_key:
        console.print("[bold red]Error:[/] ELASTIC_URL or ELASTIC_API_KEY not found in .env")
        return

    console.print(f"[bold cyan]Maintenance:[/] Purging learned runbooks from [white]{index}[/]...")
    
    # Query to find only agent-generated runbooks
    query = {
        "query": {
            "term": {
                "source.keyword": "agent_learning"
            }
        }
    }
    
    url = f"{es_url}/{index}/_delete_by_query"
    data = json.dumps(query).encode("utf-8")
    
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"ApiKey {api_key}")
    req.add_header("Content-Type", "application/json")
    
    try:
        with urllib.request.urlopen(req) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            deleted = res.get("deleted", 0)
            console.print(f"[bold green]Success![/] Removed [bold]{deleted}[/] learned runbooks.")
            console.print("[dim]Original bootstrap runbooks were preserved.[/dim]")
    except urllib.error.HTTPError as e:
        console.print(f"[bold red]Failed:[/] HTTP {e.code} - {e.reason}")
    except Exception as e:
        console.print(f"[bold red]Error:[/] {str(e)}")

if __name__ == "__main__":
    purge()
