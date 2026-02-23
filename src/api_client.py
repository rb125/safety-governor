from __future__ import annotations

import os
import httpx
from statistics import mean
from typing import Dict, List, Optional
from dataclasses import dataclass

@dataclass
class ModelReliabilityProfile:
    model_name: str
    # DDFT Metrics (Epistemic Robustness)
    hoc: float = 0.0
    ci: float = 0.0
    # CDCT Metrics (Compression Robustness)
    u_curve_magnitude: float = 0.0
    cdct_metric_source: str = "none"
    instruction_ambiguity_threshold: float = 0.5
    # EECT / AGT Metrics (Action-Gating)
    as_score: float = 0.0
    act_rate: float = 0.0
    ecs: float = 0.0

class ReliabilityAPIClient:
    """
    Client for CDCT (8001), DDFT (8002), and EECT (8003) research APIs.
    """
    def __init__(self):
        # Default to localhost if env vars not set
        self.cdct_url = os.getenv("CDCT_API_URL", "http://localhost:8001")
        self.ddft_url = os.getenv("DDFT_API_URL", "http://localhost:8002")
        self.eect_url = os.getenv("EECT_API_URL", "http://localhost:8003")
        self.timeout = httpx.Timeout(10.0, connect=5.0)

    def get_model_profile(self, model_name: str) -> ModelReliabilityProfile:
        """Fetches a combined reliability profile for a model across all research APIs."""
        profile = ModelReliabilityProfile(model_name=model_name)
        print(f"[Profile] Starting profile fetch for model '{model_name}'")
        
        with httpx.Client(timeout=self.timeout) as client:
            # 1. Fetch DDFT Metrics (Epistemic Robustness - port 8002)
            try:
                ddft_endpoint = f"{self.ddft_url}/score/{model_name}"
                print(f"[Profile] Calling DDFT endpoint: {ddft_endpoint}")
                r = client.get(ddft_endpoint)
                if r.status_code == 200:
                    data = r.json()
                    profile.hoc, profile.ci = self._extract_ddft_metrics(data)
            except Exception as e:
                print(f"Warning: Could not fetch DDFT metrics for {model_name}: {e}")

            # 2. Fetch CDCT Metrics (Compression Robustness - port 8001)
            try:
                cdct_endpoint = f"{self.cdct_url}/score/{model_name}"
                print(f"[Profile] Calling CDCT endpoint: {cdct_endpoint}")
                r = client.get(cdct_endpoint)
                if r.status_code == 200:
                    data = r.json()
                    # CDCT service may return either a dict or a list of score records.
                    profile.u_curve_magnitude, profile.cdct_metric_source = self._extract_cdct_metric(data)
                    if profile.u_curve_magnitude == 0.0:
                        print(
                            "[Profile] CDCT returned u_curve_magnitude=0.0 "
                            f"for model '{model_name}' (source={profile.cdct_metric_source})."
                        )
            except Exception as e:
                print(f"Warning: Could not fetch CDCT metrics for {model_name}: {e}")

            # 3. Fetch EECT Metrics (Action-Gating / AGT - port 8003)
            try:
                eect_endpoint = f"{self.eect_url}/score/{model_name}"
                print(f"[Profile] Calling EECT endpoint: {eect_endpoint}")
                r = client.get(eect_endpoint)
                if r.status_code == 200:
                    data = r.json()
                    profile.as_score, profile.act_rate, profile.ecs = self._extract_eect_metrics(data)
            except Exception as e:
                print(f"Warning: Could not fetch EECT metrics for {model_name}: {e}")

        print(
            "[Profile] Fetch complete: "
            f"HOC={profile.hoc}, CI={profile.ci}, "
            f"UCurve={profile.u_curve_magnitude} ({profile.cdct_metric_source}), "
            f"AS={profile.as_score}, ECS={profile.ecs}"
        )
        return profile

    @staticmethod
    def _extract_cdct_metric(payload) -> tuple[float, str]:
        if isinstance(payload, dict):
            value = payload.get("u_curve_magnitude", 0.0)
            try:
                return float(value), "u_curve_magnitude"
            except Exception:
                return 0.0, "u_curve_magnitude"
        if isinstance(payload, list):
            # Prefer the first record that contains u_curve_magnitude.
            for item in payload:
                if isinstance(item, dict) and "u_curve_magnitude" in item:
                    try:
                        return float(item.get("u_curve_magnitude", 0.0)), "u_curve_magnitude"
                    except Exception:
                        return 0.0, "u_curve_magnitude"
            # Fallback for CDCT list schema without u_curve_magnitude.
            # Build a normalized proxy score from concept-level stability/consistency fields.
            rows = [x for x in payload if isinstance(x, dict)]
            if not rows:
                return 0.0

            def _vals(key: str) -> List[float]:
                out: List[float] = []
                for r in rows:
                    if key in r:
                        try:
                            out.append(float(r.get(key, 0.0)))
                        except Exception:
                            continue
                return out

            sf_vals = [abs(v) for v in _vals("SF")]
            cri_vals = _vals("CRI")
            sas_vals = _vals("SAS_prime")
            far_vals = _vals("FAR_prime")

            if not any([sf_vals, cri_vals, sas_vals, far_vals]):
                return 0.0, "none"

            avg_sf = mean(sf_vals) if sf_vals else 0.0
            avg_cri = mean(cri_vals) if cri_vals else 1.0
            avg_sas = mean(sas_vals) if sas_vals else 1.0
            avg_far = mean(far_vals) if far_vals else 0.0

            # Proxy intent:
            # - higher |SF| => stronger context sensitivity
            # - lower CRI / SAS => weaker robustness under compression
            # - FAR' contributes lightly as a fragility signal
            proxy = (
                (0.50 * avg_sf)
                + (0.25 * (1.0 - avg_cri))
                + (0.20 * (1.0 - avg_sas))
                + (0.05 * avg_far)
            )
            return max(0.0, min(1.0, round(float(proxy), 6))), "proxy_sf_cri_sas_far"
        return 0.0, "none"

    @staticmethod
    def _extract_ddft_metrics(payload) -> tuple[float, float]:
        if not isinstance(payload, dict):
            return 0.0, 0.0
        # Accept multiple known schemas:
        # 1) {"HOC": ..., "CI": ...}
        # 2) {"AS": ..., "ER": ...}
        # 3) {"details": {"HOC": ..., "CI": ...}}
        details = payload.get("details", {}) if isinstance(payload.get("details"), dict) else {}

        hoc = payload.get("HOC")
        ci = payload.get("CI")
        if hoc is None:
            hoc = payload.get("AS")
        if ci is None:
            ci = payload.get("ER")
        if hoc is None:
            hoc = details.get("HOC")
        if ci is None:
            ci = details.get("CI")

        try:
            hoc_f = float(hoc) if hoc is not None else 0.0
        except Exception:
            hoc_f = 0.0
        try:
            ci_f = float(ci) if ci is not None else 0.0
        except Exception:
            ci_f = 0.0
        return hoc_f, ci_f

    @staticmethod
    def _extract_eect_metrics(payload) -> tuple[float, float, float]:
        if not isinstance(payload, dict):
            return 0.0, 0.0, 0.0
        # Accept multiple known schemas:
        # - {"AS": ..., "ACT Rate": ..., "ECS": ...}
        # - {"as_score": ..., "ecs": ..., "stability_index": ...}
        as_raw = payload.get("AS", payload.get("as_score", 0.0))
        ecs_raw = payload.get("ECS", payload.get("ecs", 0.0))
        act_raw = payload.get("ACT Rate", payload.get("act_rate", payload.get("stability_index", 0.0)))

        try:
            as_f = float(as_raw)
        except Exception:
            as_f = 0.0
        try:
            act_f = float(act_raw)
        except Exception:
            act_f = 0.0
        try:
            ecs_f = float(ecs_raw)
        except Exception:
            ecs_f = 0.0
        return as_f, act_f, ecs_f

    def trigger_experiment(self, service: str, model_name: str, concepts: List[str]):
        """Triggers a background diagnostic experiment on a specific service."""
        urls = {
            "cdct": self.cdct_url,
            "ddft": self.ddft_url,
            "eect": self.eect_url
        }
        if service not in urls:
            raise ValueError(f"Unknown service: {service}")
            
        try:
            with httpx.Client(timeout=self.timeout) as client:
                client.post(
                    f"{urls[service]}/run_experiment",
                    json={"model_name": model_name, "concepts": concepts}
                )
        except Exception as e:
            print(f"Error triggering {service} experiment: {e}")
