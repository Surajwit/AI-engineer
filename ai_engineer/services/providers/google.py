import json

import frappe

from .common import api_key, get_settings, post_json


def _contents(messages):
    result = []

    for message in messages:
        role = message.get("role")

        if role == "system":
            continue

        if role not in ("user", "assistant"):
            continue

        content = str(message.get("content", ""))

        result.append({
            "role": "model" if role == "assistant" else "user",
            "parts": [
                {
                    "text": content
                }
            ],
        })

    return result


def _extract_text(data):
    """
    Extract normal text output from a Gemini generateContent response.

    Gemini may return multiple parts, including thinking/reasoning parts.
    We only return parts containing user-visible text.
    """

    if not isinstance(data, dict):
        return None

    candidates = data.get("candidates")

    if not isinstance(candidates, list) or not candidates:
        return None

    texts = []

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue

        content = candidate.get("content")

        if not isinstance(content, dict):
            continue

        parts = content.get("parts")

        if not isinstance(parts, list):
            continue

        for part in parts:
            if not isinstance(part, dict):
                continue

            text = part.get("text")

            if isinstance(text, str) and text:
                texts.append(text)

    if texts:
        return "".join(texts)

    return None


def call(messages):
    s = get_settings()

    key = api_key(
        s,
        "google_api_key",
        "Google AI API Key",
    )

    model = s.model or "gemini-3.7-flash"

    base_url = (
        s.google_base_url
        or "https://generativelanguage.googleapis.com/v1beta"
    ).rstrip("/")

    url = f"{base_url}/models/{model}:generateContent"

    system_parts = [
        {
            "text": str(message.get("content", ""))
        }
        for message in messages
        if message.get("role") == "system"
    ]

    payload = {
        "contents": _contents(messages),
        "generationConfig": {
            "maxOutputTokens": int(s.max_tokens or 8192),
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "action": {
                        "type": "STRING",
                        "enum": [
                            "inspect_apps",
                            "inspect_doctype",
                            "search_code",
                            "read_file",
                            "create_app",
                            "write_file",
                            "validate_app",
                            "register_app",
                            "install_app",
                            "run_command",
                            "verify_app",
                            "finish"
                        ]
                    },
                    "args": {
                        "type": "OBJECT",
                        "properties": {
                            "doctype": {"type": "STRING"},
                            "query": {"type": "STRING"},
                            "app": {"type": "STRING"},
                            "path": {"type": "STRING"},
                            "app_name": {"type": "STRING"},
                            "title": {"type": "STRING"},
                            "description": {"type": "STRING"},
                            "content": {"type": "STRING"},
                            "command": {"type": "STRING"},
                            "cwd": {"type": "STRING"},
                            "answer": {"type": "STRING"}
                        }
                    },
                    "message": {
                        "type": "STRING"
                    }
                },
                "required": ["action", "args", "message"]
            }
        },
    }

    if system_parts:
        payload["systemInstruction"] = {
            "parts": system_parts
        }

    data = post_json(
        url,
        {
            "Content-Type": "application/json",
            "x-goog-api-key": key,
        },
        payload,
        "Google Gemini",
    )

    text = _extract_text(data)

    if text:
        return text

    # Do not hide the actual Gemini response during development.
    detail = json.dumps(
        data,
        ensure_ascii=False,
        indent=2,
        default=str,
    )

    frappe.throw(
        "Google Gemini returned no usable text response. "
        f"Raw response: {detail[:6000]}"
    )
