import frappe
from ai_engineer.services.agent import run_agent, list_apps, search_code, read_file, workspace_root


def _manager():
    if frappe.session.user == "Guest" or not "System Manager" in frappe.get_roles():
        frappe.throw("AI Engineer requires the System Manager role")


def has_app_permission():
    return frappe.session.user != "Guest" and "System Manager" in frappe.get_roles()


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
    return {
        "base_url": s.base_url,
        "model": s.model,
        "temperature": s.temperature,
        "max_tokens": s.max_tokens,
        "allow_code_execution": s.allow_code_execution,
        "has_api_key": bool(s.nvidia_api_key),
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
