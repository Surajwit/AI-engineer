from .common import api_key, get_settings, post_json


def call(messages):
    s = get_settings()
    key = api_key(s, "openai_api_key", "OpenAI API Key")
    url = (s.openai_base_url or "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
    model = s.model or "gpt-5.6-luna"
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": int(s.max_tokens or 8192),
    }
    # OpenAI reasoning models may reject temperature; omit it for broad model compatibility.
    data = post_json(
        url,
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        payload,
        "OpenAI",
    )
    try:
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, str):
            return content
        return str(content)
    except (KeyError, IndexError, TypeError):
        import frappe
        frappe.throw("OpenAI API returned an unexpected response")
