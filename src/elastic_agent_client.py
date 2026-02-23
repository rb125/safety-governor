from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
from typing import Dict, Any, List, Optional

class ElasticAgentClient:
    """
    Client for interacting with the Elastic Agent Builder REST API.
    """
    def __init__(self, kibana_url: str, api_key: str):
        self.kibana_url = kibana_url.rstrip("/")
        self.api_key = api_key

    def chat(self, agent_id: str, message: str, conversation_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Sends a message to an Elastic Agent and returns the response.
        """
        if not self.kibana_url:
            raise ValueError("Kibana URL is not configured.")

        # Updated endpoint for the Agent Builder Converse API
        path = "/api/agent_builder/converse"
        url = f"{self.kibana_url}{path}"
        
        payload = {
            "input": message,
            "agent_id": agent_id,
        }
        if conversation_id:
            payload["conversation_id"] = conversation_id

        return self._request("POST", path, payload)

    def _request(self, method: str, path: str, payload: Dict) -> Dict:
        url = f"{self.kibana_url}{path}"
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url=url, data=body, method=method)
        req.add_header("Authorization", f"ApiKey {self.api_key}")
        req.add_header("Content-Type", "application/json")
        req.add_header("kbn-xsrf", "true") # Required for Kibana APIs

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            details = e.read().decode("utf-8", errors="replace")
            print(f"Kibana API Error {e.code}: {details}")
            raise
        except Exception as e:
            print(f"Kibana Connection Error: {e}")
            raise
