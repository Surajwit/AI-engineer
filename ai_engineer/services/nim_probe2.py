import time
import frappe
import requests

def run():
    s = frappe.get_doc("AI Engineer Settings")
    key = s.get_password("nvidia_api_key")
    url = (s.base_url or "https://integrate.api.nvidia.com/v1").rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    payload = {
        "model": "moonshotai/kimi-k2.6",
        "messages": [{"role": "user", "content": "Reply with exactly: ok"}],
        "max_tokens": 50,
        "stream": False,
    }
    start = time.time()
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=60)
        print(f"HTTP {r.status_code} in {time.time()-start:.1f}s")
        print(r.text[:500])
    except requests.exceptions.ReadTimeout:
        print(f"TIMED OUT after {time.time()-start:.1f}s")
