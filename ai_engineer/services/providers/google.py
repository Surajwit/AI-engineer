from .common import api_key, get_settings, post_json


def _contents(messages):
    result = []
    for message in messages:
        role = message.get("role")
        if role == "system":
            continue
        if role not in ("user", "assistant"):
            continue
        result.append({
            "role": "model" if role == "assistant" else "user",
            "parts": [{"text": str(message.get("content", ""))}],
        })
    return result


def call(messages):
    s = get_settings()
    key = api_key(s, "google_api_key", "Google AI API Key")
    model = s.model or "gemini-3.7-flash"
    base_url = (s.google_base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
    url = f"{base_url}/models/{model}:generateContent"

    system_parts = [
        {"text": str(message.get("content", ""))}
        for message in messages
        if message.get("role") == "system"
    ]
    payload = {
        "contents": _contents(messages),
        "generationConfig": {
            "temperature": float(s.temperature or 0.1),
            "maxOutputTokens": int(s.max_tokens or 8192),
        },
    }
    if system_parts:
        payload["systemInstruction"] = {"parts": system_parts}

    data = post_json(
        url,
        {"Content-Type": "application/json", "x-goog-api-key": key},
        payload,
        "Google Gemini",
    )
    try:
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(part.get("text", "") for part in parts if part.get("text"))
        if text:
            return text
    except (KeyError, IndexError, TypeError):
        pass
    import frappe
    frappe.throw("Google Gemini API returned an unexpected response")
