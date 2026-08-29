import json
import os
import re
import subprocess
from pathlib import Path

import frappe
import requests


MAX_FILE_BYTES = 200_000
MAX_OUTPUT = 20_000
SAFE_NAME = re.compile(r"^[a-z][a-z0-9_]{1,39}$")


def settings():
    return frappe.get_single("AI Engineer Settings")


def bench_root():
    frappe_path = Path(frappe.get_app_path("frappe"))
    return frappe_path.parents[2]


def apps_root():
    return bench_root() / "apps"


def workspace_root():
    s = settings()
    if s.workspace_root:
        p = Path(s.workspace_root).expanduser().resolve()
        try:
            p.relative_to(bench_root().resolve())
        except ValueError:
            frappe.throw("Workspace root must be inside the Frappe bench")
        p.mkdir(parents=True, exist_ok=True)
        return p
    p = Path(frappe.get_site_path("private", "ai_engineer")).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def safe_path(path, allowed_roots):
    p = Path(path).expanduser().resolve()
    for root in allowed_roots:
        root = Path(root).resolve()
        try:
            p.relative_to(root)
            return p
        except ValueError:
            pass
    frappe.throw("Path is outside the AI Engineer allowed workspace")


def list_apps():
    result = []
    for app in frappe.get_installed_apps():
        try:
            path = Path(frappe.get_app_path(app)).resolve()
            result.append({"app": app, "path": str(path)})
        except Exception:
            result.append({"app": app})
    return result


def search_code(query, app=None, max_results=40):
    if not query or len(query) > 200:
        frappe.throw("Invalid search query")
    roots = [apps_root()]
    if app:
        if not SAFE_NAME.match(app):
            frappe.throw("Invalid app name")
        roots = [apps_root() / app]
    root = safe_path(roots[0], [apps_root()])
    if not root.is_dir():
        frappe.throw("App directory not found")
    cmd = ["rg", "-n", "--hidden", "--glob", "!.git", "--glob", "!node_modules", "--glob", "!*.map", query, str(root)]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return p.stdout[:MAX_OUTPUT]
    except FileNotFoundError:
        return "ripgrep is not installed"
    except subprocess.TimeoutExpired:
        return "Search timed out"


def read_file(path):
    p = safe_path(path, [apps_root(), workspace_root()])
    if not p.is_file():
        frappe.throw("File not found")
    if p.stat().st_size > MAX_FILE_BYTES:
        frappe.throw("File is too large; search or request a smaller file")
    return p.read_text(encoding="utf-8", errors="replace")


def write_generated_file(path, content):
    root = workspace_root()
    p = safe_path(path, [root])
    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        frappe.throw("Generated file exceeds size limit")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return str(p)


def create_app_skeleton(app_name, title=None, description=None):
    if not SAFE_NAME.match(app_name):
        frappe.throw("App name must be lowercase snake_case, 2-40 chars")
    target = safe_path(apps_root() / app_name, [apps_root()])
    if target.exists():
        frappe.throw("App already exists")
    title = title or app_name.replace("_", " ").title()
    description = description or "AI-generated Frappe application"
    pkg = target / app_name
    (pkg / "doctype").mkdir(parents=True)
    (pkg / "api").mkdir()
    (pkg / "public" / "js").mkdir(parents=True)
    (pkg / "public" / "css").mkdir(parents=True)
    files = {
        "hooks.py": f'app_name = "{app_name}"\napp_title = "{title}"\napp_publisher = "{title}"\napp_description = "{description}"\napp_email = "dev@example.com"\napp_license = "MIT"\n',
        "modules.txt": title + "\n",
        "patches.txt": "",
        "requirements.txt": "",
        "__init__.py": '__version__ = "0.1.0"\n',
        f"{app_name}/__init__.py": "",
    }
    for rel, content in files.items():
        p = target / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return {"app": app_name, "path": str(target), "files": list(files)}


def validate_generated_app(app_name):
    target = safe_path(apps_root() / app_name, [apps_root()])
    if not target.exists():
        frappe.throw("App does not exist")
    py_files = list(target.rglob("*.py"))
    errors = []
    for p in py_files:
        try:
            compile(p.read_text(encoding="utf-8"), str(p), "exec")
        except SyntaxError as e:
            errors.append(f"{p}: {e}")
    return {"files": len(py_files), "syntax_errors": errors, "valid": not errors}


def run_allowed(command, cwd):
    s = settings()
    if not s.allow_code_execution:
        frappe.throw("Code execution is disabled in AI Engineer Settings")
    allowed = [x.strip() for x in (s.allowed_commands or "").splitlines() if x.strip()]
    if not any(command == x or command.startswith(x + " ") for x in allowed):
        frappe.throw("Command is not in the allowlist")
    if any(token in command for token in [";", "&&", "||", "|", "`", "$(", ">", "<"]):
        frappe.throw("Shell operators are not allowed in AI Engineer commands")
    cwd = safe_path(cwd, [apps_root(), workspace_root()])
    p = subprocess.run(command, cwd=cwd, shell=True, capture_output=True, text=True, timeout=120)
    return {"returncode": p.returncode, "stdout": p.stdout[-MAX_OUTPUT:], "stderr": p.stderr[-MAX_OUTPUT:]}


def active_provider():
    provider = (settings().ai_provider or "NVIDIA").strip()
    allowed = {"Anthropic", "Google", "NVIDIA", "OpenAI"}
    if provider not in allowed:
        frappe.throw(f"Unsupported AI provider: {provider}")
    return provider


def call_model(messages):
    """Route a model request through the provider selected in AI Engineer Settings."""
    provider = active_provider()
    from ai_engineer.services.providers import (
        call_anthropic,
        call_google,
        call_nvidia,
        call_openai,
    )
    handlers = {
        "Anthropic": call_anthropic,
        "Google": call_google,
        "NVIDIA": call_nvidia,
        "OpenAI": call_openai,
    }
    return handlers[provider](messages)


def call_nim(messages):
    """Backward-compatible NVIDIA entry point for older integrations."""
    from ai_engineer.services.providers import call_nvidia
    return call_nvidia(messages)



def system_prompt():
    return """You are AI Engineer, a senior Frappe/ERPNext software engineer. You know Python, JavaScript, TypeScript, React, SQL, MariaDB/Postgres, Redis, RQ, Docker, Linux, Git, Frappe Framework and ERPNext architecture. You work methodically: inspect first, reason, plan, generate small coherent changes, validate, then report. Never invent repository APIs when inspection can answer the question. Respect Frappe permissions and accounting/ledger integrity. Never modify ERPNext/Frappe core directly when an installable custom app is appropriate.

You can request one tool action at a time by returning ONLY JSON in this form:
{"action":"inspect_apps|search_code|read_file|create_app|write_file|validate_app|run_command|finish","args":{...},"message":"brief reason"}
Allowed args:
inspect_apps: {}
search_code: {"query":"...","app":"optional"}
read_file: {"path":"absolute path"}
create_app: {"app_name":"snake_case","title":"...","description":"..."}
write_file: {"path":"absolute path inside generated app/workspace","content":"..."}
validate_app: {"app_name":"..."}
run_command: {"command":"exact allowlisted command","cwd":"absolute path"}
finish: {"answer":"..."}

For app creation, first inspect relevant existing code. Then create the skeleton, write all required files, validate it, and explain any command that requires explicit user approval. Do not execute destructive operations, install packages, modify existing apps, git push, or database migrations without explicit approval. The user may ask for an entire app; produce production-quality structure rather than a toy example."""


def parse_action(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and obj.get("action"):
            return obj
    except Exception:
        pass
    return {"action": "finish", "args": {"answer": text}, "message": "model returned prose"}


def execute_action(action):
    name = action.get("action")
    args = action.get("args") or {}
    if name == "inspect_apps": return list_apps()
    if name == "search_code": return search_code(args.get("query"), args.get("app"))
    if name == "read_file": return read_file(args.get("path"))
    if name == "create_app": return create_app_skeleton(args.get("app_name"), args.get("title"), args.get("description"))
    if name == "write_file": return write_generated_file(args.get("path"), args.get("content", ""))
    if name == "validate_app": return validate_generated_app(args.get("app_name"))
    if name == "run_command": return run_allowed(args.get("command", ""), args.get("cwd"))
    return None


def run_agent(session_id, user_message, max_steps=12):
    session = frappe.get_doc("AI Engineer Session", session_id)
    history = json.loads(session.messages or "[]")
    messages = [{"role":"system","content":system_prompt()}]
    messages.extend(history[-20:])
    messages.append({"role":"user","content":user_message})
    for _ in range(max_steps):
        raw = call_model(messages)
        action = parse_action(raw)
        messages.append({"role":"assistant","content":raw})
        if action["action"] == "finish":
            answer = action.get("args", {}).get("answer") or action.get("message") or raw
            history.extend([{"role":"user","content":user_message},{"role":"assistant","content":answer}])
            session.messages = json.dumps(history[-40:])
            session.status = "Completed"
            session.last_action = "finish"
            session.save(ignore_permissions=True)
            return {"answer": answer, "steps": _ + 1}
        try:
            result = execute_action(action)
        except Exception as e:
            result = {"error": str(e)}
            action_status = "Failed"
        else:
            action_status = "Executed"
        frappe.get_doc({"doctype":"AI Engineer Action","session":session.name,"action":action["action"],"target":str(action.get("args",{}).get("path") or action.get("args",{}).get("app_name") or ""),"input_json":json.dumps(action.get("args",{})),"output":json.dumps(result, default=str)[:MAX_OUTPUT],"status":action_status}).insert(ignore_permissions=True)
        messages.append({"role":"user","content":"TOOL RESULT:\n" + json.dumps(result, default=str)[:MAX_OUTPUT]})
    frappe.throw("AI agent reached its step limit; continue the task from the conversation")
