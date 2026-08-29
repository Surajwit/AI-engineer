from .common import api_key, get_settings, post_json


def _split_system(messages):
    system_parts = []
    conversation = []
    for message in messages:
        role = message.get("role")
        content = message.get("content", "")
        if role == "system":
            system_parts.append(content)
        elif role in ("user", "assistant"):
            conversation.append({"role": role, "content": content})
    return "\n\n".join(str(part) for part in system_parts if part), conversation


def call(messages):
    s = get_settings()
    key = api_key(s, "anthropic_api_key", "Anthropic API Key")
    url = (s.anthropic_base_url or "https://api.anthropic.com").rstrip("/") + "/v1/messages"
    model = s.model or "claude-sonnet-5"
    system, conversation = _split_system(messages)
    payload = {
        "model": model,
        "max_tokens": int(s.max_tokens or 8192),
        "messages": conversation,
    }
    if system:
        payload["system"] = system

    # Do not send temperature: current Claude models such as Sonnet 5 reject non-default sampling parameters.
    data = post_json(
        url,
        {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        payload,
        "Anthropic",
    )
    try:
        blocks = data["content"]
        text = "".join(block.get("text", "") for block in blocks if block.get("type") == "text")
        if text:
            return text
    except (KeyError, TypeError):
        pass
    import frappe
    frappe.throw("Anthropic API returned an unexpected response")
