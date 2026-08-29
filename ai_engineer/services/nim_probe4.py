import frappe
import requests

def run():
    s = frappe.get_doc("AI Engineer Settings")
    key = s.get_password("nvidia_api_key")
    url = (s.base_url or "https://integrate.api.nvidia.com/v1").rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {key}"}
    r = requests.get(url, headers=headers, timeout=30)
    print("HTTP", r.status_code)
    print(r.text[:3000])
