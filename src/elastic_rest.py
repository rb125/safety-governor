from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Dict, List


@dataclass
class SearchHit:
    doc_id: str
    score: float
    source: Dict


class ElasticRestClient:
    """
    Minimal Elasticsearch REST adapter used by the reliability layer.

    Required env/config:
    - base_url: e.g. https://<cluster-id>.<region>.aws.found.io:443
    - api_key: Elasticsearch API key (id:key encoded token)
    - index_map: logical to physical index mapping, e.g. {"runbooks": "runbooks-*"}
    """

    def __init__(self, base_url: str, api_key: str, index_map: Dict[str, str]):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.index_map = index_map

    def hybrid_search(
        self, index: str, query: str, top_k: int = 3, filters: Dict | None = None
    ) -> List[SearchHit]:
        target_index = self._resolve_index(index)
        payload = {
            "size": top_k,
            "query": {
                "bool": {
                    "must": [
                        {
                            "multi_match": {
                                "query": query,
                                "fields": ["title^2", "body", "text", "summary", "symptoms", "rule"],
                                "type": "best_fields",
                            }
                        }
                    ],
                    "filter": self._filter_clauses(filters),
                }
            },
        }

        data = self._request_json("POST", f"/{target_index}/_search", payload)
        hits = data.get("hits", {}).get("hits", [])
        out: List[SearchHit] = []
        for hit in hits:
            out.append(
                SearchHit(
                    doc_id=hit.get("_id", ""),
                    score=float(hit.get("_score", 0.0) or 0.0),
                    source=hit.get("_source", {}),
                )
            )
        return out

    def esql_policy_conflicts(self, service: str, action: str, severity: str) -> List[str]:
        target_index = self._resolve_index("policies")
        action_str = str(action).lower()
        severity_str = str(severity).lower()

        esql = (
            f"FROM {target_index} "
            f"| WHERE (service == \"*\" OR service == \"{service}\") "
            f"AND severities LIKE \"*{severity_str}*\" "
            f"AND blocked_actions LIKE \"*{action_str}*\" "
            "| KEEP id"
        )

        try:
            data = self._request_json("POST", "/_query", {"query": esql})
            values = data.get("values", [])
            return [row[0] for row in values if row]
        except Exception:
            payload = {
                "size": 20,
                "query": {
                    "bool": {
                        "must": [
                            {
                                "bool": {
                                    "should": [
                                        {"term": {"service": "*"}},
                                        {"term": {"service": service}},
                                    ],
                                    "minimum_should_match": 1,
                                }
                            },
                            {"term": {"severities": severity_str}},
                            {"term": {"blocked_actions": action_str}},
                        ]
                    }
                },
            }
            data = self._request_json("POST", f"/{target_index}/_search", payload)
            hits = data.get("hits", {}).get("hits", [])
            conflicts: List[str] = []
            for hit in hits:
                src = hit.get("_source", {})
                conflicts.append(src.get("id", hit.get("_id", "unknown")))
            return conflicts

    def index_document(self, index: str, document: Dict, doc_id: str | None = None) -> Dict:
        target_index = self._resolve_index(index)
        if doc_id:
            path = f"/{target_index}/_doc/{urllib.parse.quote(doc_id)}"
            return self._request_json("PUT", path, document)
        return self._request_json("POST", f"/{target_index}/_doc", document)

    def _resolve_index(self, logical: str) -> str:
        return self.index_map.get(logical, logical)

    @staticmethod
    def _filter_clauses(filters: Dict | None) -> List[Dict]:
        if not filters:
            return []
        clauses: List[Dict] = []
        for key, value in filters.items():
            if isinstance(value, list):
                clauses.append({"terms": {key: value}})
            else:
                clauses.append({"term": {key: value}})
        return clauses

    def _request_json(self, method: str, path: str, payload: Dict) -> Dict:
        url = f"{self.base_url}{path}"
        body = json.dumps(payload).encode("utf-8")
        attempts = 3
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            req = urllib.request.Request(url=url, data=body, method=method)
            req.add_header("Authorization", f"ApiKey {self.api_key}")
            req.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    raw = resp.read().decode("utf-8")
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as e:
                details = e.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"Elasticsearch HTTP {e.code} on {path}: {details}") from e
            except urllib.error.URLError as e:
                last_error = e
                if attempt < attempts:
                    time.sleep(1.0 * attempt)
                    continue
                raise RuntimeError(f"Elasticsearch network error on {path}: {e}") from e
        raise RuntimeError(f"Elasticsearch network error on {path}: {last_error}")
