import json

import frappe
import requests

DEFAULT_TIMEOUT = 280
MAX_ERROR = 2000


def get_settings():
    return frappe.get_single("AI Engineer Settings")


def api_key(settings, fieldname, label):
    if not getattr(settings, fieldname, None):
        frappe.throw(f"Configure the {label} in AI Engineer Settings")
    return settings.get_password(fieldname)


def post_json(url, headers, payload, provider, timeout=DEFAULT_TIMEOUT):
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=timeout)
    except requests.exceptions.ReadTimeout:
        frappe.throw(f"{provider} API request timed out after {timeout}s. Try a smaller prompt or max tokens value.")
    except requests.exceptions.RequestException as exc:
        frappe.throw(f"{provider} API connection error: {exc}")

    if not response.ok:
        try:
            detail = response.json()
            detail = json.dumps(detail, ensure_ascii=False)
        except ValueError:
            detail = response.text
        frappe.throw(f"{provider} API error {response.status_code}: {detail[:MAX_ERROR]}")

    try:
        return response.json()
    except ValueError:
        frappe.throw(f"{provider} API returned invalid JSON")
