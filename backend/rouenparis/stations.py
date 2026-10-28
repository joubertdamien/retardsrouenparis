from __future__ import annotations

import logging
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)


def _get_json(url: str, token: str, timeout: int = 30) -> dict:
    request = urllib.request.Request(url)
    request.add_header("Authorization", token)
    request.add_header("Accept", "application/json")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        import json

        return json.loads(response.read().decode("utf-8"))


def resolve_stop_area(api_base: str, coverage: str, token: str, uic: str) -> str:
    query = urllib.parse.urlencode({"q": uic, "type[]": "stop_area", "count": 5})
    url = f"{api_base}/coverage/{coverage}/stop_areas?{query}"
    payload = _get_json(url, token)
    for stop_area in payload.get("stop_areas", []):
        codes = stop_area.get("codes", [])
        for code in codes:
            if code.get("type") == "UIC" and str(code.get("value", "")).lstrip("0") == uic.lstrip("0"):
                return stop_area["id"]
    for stop_area in payload.get("stop_areas", []):
        admin = stop_area.get("administrative_regions", [])
        label = stop_area.get("label", "")
        if uic in label:
            return stop_area["id"]
    raise RuntimeError(f"No stop_area found for UIC {uic} on coverage {coverage}")
