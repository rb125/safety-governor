from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List


@dataclass
class SearchHit:
    doc_id: str
    score: float
    source: Dict


class ElasticMock:
    """
    Tiny in-memory stand-in for Elasticsearch hybrid retrieval and ES|QL-like filters.
    """

    def __init__(self, documents: Dict[str, List[Dict]]):
        self.documents = documents
        self.by_index = defaultdict(dict)
        for index_name, docs in documents.items():
            for doc in docs:
                self.by_index[index_name][doc["id"]] = doc

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        return [t.strip(".,:;!?()[]{}\"'").lower() for t in text.split() if t.strip()]

    def hybrid_search(
        self, index: str, query: str, top_k: int = 3, filters: Dict | None = None
    ) -> List[SearchHit]:
        query_tokens = set(self._tokenize(query))
        hits: List[SearchHit] = []
        for doc_id, doc in self.by_index[index].items():
            if filters:
                skip = False
                for k, expected in filters.items():
                    if k not in doc:
                        skip = True
                        break
                    value = doc[k]
                    if isinstance(expected, list):
                        if value not in expected:
                            skip = True
                            break
                    elif value != expected:
                        skip = True
                        break
                if skip:
                    continue
            text = " ".join(str(v) for v in doc.values() if isinstance(v, str))
            tokens = set(self._tokenize(text))
            if not tokens:
                continue
            overlap = len(query_tokens.intersection(tokens))
            score = overlap / max(len(query_tokens), 1)
            if score > 0:
                hits.append(SearchHit(doc_id=doc_id, score=score, source=doc))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]

    def esql_policy_conflicts(self, service: str, action: str, severity: str) -> List[str]:
        conflicts: List[str] = []
        for policy in self.documents.get("policies", []):
            applies = (
                policy.get("service") in {"*", service}
                and action.lower() in policy.get("blocked_actions", [])
                and severity.lower() in policy.get("severities", [])
            )
            if applies:
                conflicts.append(policy["id"])
        return conflicts

    def get_doc(self, index: str, doc_id: str) -> Dict:
        return self.by_index[index][doc_id]

    def index_document(self, index: str, document: Dict, doc_id: str | None = None) -> Dict:
        target_id = doc_id or f"{index}-{len(self.by_index[index]) + 1}"
        doc = dict(document)
        doc.setdefault("id", target_id)
        self.documents.setdefault(index, []).append(doc)
        self.by_index[index][target_id] = doc
        return {"result": "created", "_id": target_id}

    def _request_json(self, method: str, path: str, payload: Dict) -> Dict:
        if method == "POST" and path == "/kibana_sample_data_logs/_search":
            docs = self.documents.get("kibana_sample_data_logs", [])
            error_docs = [d for d in docs if int(d.get("response", 0)) >= 400]
            avg_bytes = 0.0
            if docs:
                avg_bytes = sum(float(d.get("bytes", 0)) for d in docs) / len(docs)
            return {
                "aggregations": {
                    "error_count": {"doc_count": len(error_docs)},
                    "avg_response_size": {"value": avg_bytes},
                }
            }
        raise RuntimeError(f"Unsupported mock request: {method} {path}")
