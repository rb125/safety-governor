import json
import os
import base64
import urllib.request
import urllib.error
from typing import Dict, Optional

class JiraClient:
    def __init__(self):
        self.url = os.getenv("JIRA_URL", "").rstrip("/")
        self.email = os.getenv("JIRA_EMAIL", "")
        self.token = os.getenv("JIRA_API_TOKEN", "")
        self.project = os.getenv("JIRA_PROJECT_KEY", "SRE")
        
        if self.email and self.token:
            auth_str = f"{self.email}:{self.token}"
            self.auth_header = f"Basic {base64.b64encode(auth_str.encode()).decode()}"
        else:
            self.auth_header = None

    def _request(self, method: str, path: str, payload: Optional[Dict] = None) -> Dict:
        if not self.auth_header:
            return {"error": "Jira credentials missing"}
            
        url = f"{self.url}/rest/api/3/{path}"
        req = urllib.request.Request(url, method=method)
        req.add_header("Authorization", self.auth_header)
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        
        data = json.dumps(payload).encode("utf-8") if payload else None
        
        try:
            with urllib.request.urlopen(req, data=data, timeout=10) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {"status": "ok"}
        except urllib.error.HTTPError as e:
            try:
                err_details = e.read().decode("utf-8")
                return {"error": f"HTTP {e.code}", "details": err_details}
            except:
                return {"error": f"HTTP {e.code}"}
        except Exception as e:
            return {"error": str(e)}

    def search_issues(self, jql: str, max_results: int = 50) -> Dict:
        # UPDATED ENDPOINT per Atlassian migration
        payload = {
            "jql": jql,
            "maxResults": max_results,
            "fields": ["key", "summary"]
        }
        return self._request("POST", "search/jql", payload)

    def create_issue(self, summary: str, description: str, issue_type: str = "Task") -> Dict:
        payload = {
            "fields": {
                "project": {"key": self.project},
                "summary": summary,
                "description": {
                    "type": "doc",
                    "version": 1,
                    "content": [{"type": "paragraph", "content": [{"type": "text", "text": description}]}]
                },
                "issuetype": {"name": issue_type}
            }
        }
        return self._request("POST", "issue", payload)

    def add_comment(self, issue_key: str, comment: str) -> Dict:
        payload = {
            "body": {
                "type": "doc",
                "version": 1,
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": comment}]}]
            }
        }
        return self._request("POST", f"issue/{issue_key}/comment", payload)

    def delete_issue(self, issue_key: str) -> Dict:
        return self._request("DELETE", f"issue/{issue_key}")

    def resolve_issue(self, issue_key: str) -> Dict:
        transitions = self._request("GET", f"issue/{issue_key}/transitions")
        transition_id = None
        if "transitions" in transitions:
            for t in transitions["transitions"]:
                if t["name"].lower() in ["done", "resolved", "closed", "complete"]:
                    transition_id = t["id"]
                    break
        if not transition_id:
            return {"error": "Could not find 'Done' transition ID"}
        return self._request("POST", f"issue/{issue_key}/transitions", {"transition": {"id": transition_id}})
