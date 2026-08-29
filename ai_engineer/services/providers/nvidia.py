from .common import api_key, get_settings, post_json


def call(messages):
    s = get_settings()
    key = api_key(s, "nvidia_api_key", "NVIDIA API Key")
    url = (s.nvidia_base_url or s.base_url or "https://integrate.api.nvidia.com/v1").rstrip("/") + "/chat/completions"
    model = s.model or "moonshotai/kimi-k2.6"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": float(s.temperature or 0.1),
        "max_tokens": int(s.max_tokens or 8192),
        "stream": False,
    }
    if "deepseek" in model.lower():
        payload["chat_template_kwargs"] = {"thinking": True, "reasoning_effort": "low"}

    data = post_json(
        url,
        {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        payload,
        "NVIDIA",
    )
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        import frappe
        frappe.throw("NVIDIA API returned an unexpected response")
