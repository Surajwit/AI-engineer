import time
import frappe
import requests

def run():
    s = frappe.get_doc("AI Engineer Settings")
    key = s.get_password("nvidia_api_key")
    if not key:
        print("NO API KEY SET in AI Engineer Settings")
        return
    url = (s.base_url or "https://integrate.api.nvidia.com/v1").rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    tests = [
        ("thinking=true, effort=low", {"thinking": True, "reasoning_effort": "low"}),
        ("thinking=false (non-think)", {"thinking": False}),
    ]

    for label, ctk in tests:
        payload = {
            "model": "deepseek-ai/deepseek-v4-flash-0731",
            "messages": [{"role": "user", "content": "Reply with exactly: ok"}],
            "max_tokens": 50,
            "stream": False,
            "chat_template_kwargs": ctk,
        }
        print(f"\n=== {label} ===")
        start = time.time()
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=200)
            elapsed = time.time() - start
            print(f"HTTP {r.status_code} in {elapsed:.1f}s")
            print(r.text[:500])
        except requests.exceptions.ReadTimeout:
            elapsed = time.time() - start
            print(f"TIMED OUT after {elapsed:.1f}s")
        except Exception as e:
            elapsed = time.time() - start
            print(f"ERROR after {elapsed:.1f}s: {e}")
