import frappe
from ai_engineer.services.agent import run_agent, list_apps, search_code, read_file, workspace_root, active_provider, call_model


def _manager():
    if frappe.session.user == "Guest" or not frappe.has_role("System Manager"):
        frappe.throw("AI Engineer requires the System Manager role")


def has_app_permission():
    return frappe.session.user != "Guest" and frappe.has_role("System Manager")


def _get_owned_session(name):
    session = frappe.get_doc("AI Engineer Session", name)
    if session.user != frappe.session.user:
        frappe.throw("You do not have access to this AI Engineer session")
    return session


@frappe.whitelist()
def create_session(title="AI Engineer Session"):
    _manager()
    doc = frappe.get_doc({
        "doctype": "AI Engineer Session",
        "title": title,
        "user": frappe.session.user,
        "working_directory": str(workspace_root()),
        "messages": "[]",
    })
    doc.insert(ignore_permissions=True)
    return doc.name


@frappe.whitelist()
def chat(session, message):
    _manager()
    if not message or len(message) > 20000:
        frappe.throw("Message is empty or too large")
    _get_owned_session(session)
    return run_agent(session, message)


@frappe.whitelist()
def get_settings():
    _manager()
    s = frappe.get_single("AI Engineer Settings")
    provider = active_provider()
    key_fields = {
        "Anthropic": "anthropic_api_key",
        "Google": "google_api_key",
        "NVIDIA": "nvidia_api_key",
        "OpenAI": "openai_api_key",
    }
    configured = {}
    for name, fieldname in key_fields.items():
        configured[name] = bool(getattr(s, fieldname, None))
    return {
        "provider": provider,
        "model": s.model,
        "temperature": s.temperature,
        "max_tokens": s.max_tokens,
        "allow_code_execution": s.allow_code_execution,
        "has_api_key": configured.get(provider, False),
        "providers": configured,
    }


@frappe.whitelist()
def test_provider():
    _manager()
    provider = active_provider()
    model = frappe.get_single("AI Engineer Settings").model
    try:
        answer = call_model([
            {"role": "system", "content": "Reply with exactly OK."},
            {"role": "user", "content": "Connection test. Reply with exactly OK."},
        ])
        return {
            "ok": True,
            "provider": provider,
            "model": model,
            "message": f"{provider} ({model}) connection successful. Response: {answer[:200]}",
        }
    except Exception as exc:
        return {
            "ok": False,
            "provider": provider,
            "model": model,
            "message": str(exc),
        }


@frappe.whitelist()
def inspect_apps():
    _manager()
    return list_apps()


@frappe.whitelist()
def search(query, app=None):
    _manager()
    return search_code(query, app)


@frappe.whitelist()
def read(path):
    _manager()
    return read_file(path)


@frappe.whitelist()
def cleanup_old_sessions():
    frappe.db.delete(
        "AI Engineer Action",
        {"creation": ["<", frappe.utils.add_days(frappe.utils.now(), -30)]},
    )
    frappe.db.delete(
        "AI Engineer Session",
        {"creation": ["<", frappe.utils.add_days(frappe.utils.now(), -30)]},
    )
    frappe.db.commit()
