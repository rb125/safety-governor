from __future__ import annotations

import json
import os
from pathlib import Path
from dotenv import load_dotenv

from src.elastic_rest import ElasticRestClient

load_dotenv()

ROOT = Path(__file__).resolve().parent
DATA_PATH = ROOT / "data" / "sample_data.json"


def env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def main() -> None:
    base_url = env("ELASTIC_URL")
    api_key = env("ELASTIC_API_KEY")

    index_map = {
        "runbooks": os.getenv("ELASTIC_RUNBOOKS_INDEX", "runbooks-demo"),
        "evidence": os.getenv("ELASTIC_EVIDENCE_INDEX", "evidence-demo"),
        "policies": os.getenv("ELASTIC_POLICIES_INDEX", "policies-demo"),
        "incidents": os.getenv("ELASTIC_INCIDENTS_INDEX", "incidents-demo"),
    }

    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    client = ElasticRestClient(base_url=base_url, api_key=api_key, index_map=index_map)

    for index in ("runbooks", "evidence", "policies", "incidents"):
        for doc in data.get(index, []):
            doc_id = doc.get("id")
            client.index_document(index=index, document=doc, doc_id=doc_id)

    print("Indexed sample documents into Elasticsearch:")
    print(json.dumps(index_map, indent=2))


if __name__ == "__main__":
    main()
