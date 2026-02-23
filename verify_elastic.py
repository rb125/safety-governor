from __future__ import annotations

import os
import json
from src.elastic_rest import ElasticRestClient
from dotenv import load_dotenv

load_dotenv()

def main():
    base_url = os.getenv("ELASTIC_URL")
    api_key = os.getenv("ELASTIC_API_KEY")
    
    if not base_url or not api_key:
        print("ELASTIC_URL or ELASTIC_API_KEY not set in environment.")
        return

    index_map = {
        "runbooks": "runbooks-demo",
        "evidence": "evidence-demo",
        "logs": "kibana_sample_data_logs"
    }
    
    client = ElasticRestClient(base_url=base_url, api_key=api_key, index_map=index_map)
    
    print("\n--- Inspecting Web Logs (Errors) ---")
    try:
        # Search for recent 500 errors to find a realistic failure mode
        query = {
            "size": 3,
            "query": {
                "range": {
                    "response": {"gte": 500}
                }
            }
        }
        res = client._request_json("POST", "/kibana_sample_data_logs/_search", query)
        hits = res.get("hits", {}).get("hits", [])
        for hit in hits:
            src = hit.get("_source", {})
            print(f"Error: {src.get('response')} | URL: {src.get('url')} | Agent: {src.get('agent')}")
    except Exception as e:
        print(f"Failed to query logs: {e}")

if __name__ == "__main__":
    main()
