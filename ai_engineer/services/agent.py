import importlib
import json
import os
import re
import subprocess
import sys
import shutil
from pathlib import Path

import frappe
import requests
from ai_engineer.services.providers.common import ProviderRateLimitError


MAX_FILE_BYTES = 200_000
MAX_OUTPUT = 20_000
SAFE_NAME = re.compile(r"^[a-z][a-z0-9_]{1,39}$")


def settings():
    return frappe.get_single("AI Engineer Settings")


def get_configured_site():
    """
    Return the ONLY Frappe site AI Engineer is authorized to operate on.

    AI Engineer must never fall back to frappe.local.site.
    """
    site = (settings().site_name or "").strip()

    if not site:
        frappe.throw(
            "Site Name is not configured in AI Engineer Settings. "
            "Please configure the Site Name before running AI Engineer "
            "site operations."
        )

    if not re.fullmatch(r"[a-zA-Z0-9_.-]+", site):
        frappe.throw(
            "Invalid Site Name in AI Engineer Settings. "
            "Use only letters, numbers, dots, underscores, and hyphens."
        )

    return site


def bench_root():
    frappe_path = Path(frappe.get_app_path("frappe"))
    return frappe_path.parents[2]


def bench_python():
    """
    Return the Python interpreter belonging to this Frappe Bench.

    Bench's Python environment contains the Frappe/ERPNext application
    packages. The Bench package itself is exposed separately through
    PYTHONPATH when invoking Bench APIs.
    """
    python_path = bench_root() / "env" / "bin" / "python"

    if not python_path.is_file():
        frappe.throw(
            f"Bench Python executable was not found: {python_path}"
        )

    return str(python_path)




def apps_root():
    return bench_root() / "apps"


def workspace_root():
    s = settings()
    bench = bench_root().resolve()

    if s.workspace_root:
        p = Path(s.workspace_root).expanduser().resolve()
        try:
            p.relative_to(bench)
        except ValueError:
            frappe.throw("Workspace root must be inside the Frappe bench")
        p.mkdir(parents=True, exist_ok=True)
        return p

    # Default AI Engineer workspace is the Frappe bench.
    # This allows the agent to inspect the complete bench while
    # safe_path() still prevents access outside the bench.
    return bench


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



def inspect_doctype(doctype):
    """Inspect a Frappe DocType using its actual metadata."""
    if not doctype or len(doctype) > 140:
        frappe.throw("Invalid DocType name")

    try:
        meta = frappe.get_meta(doctype)
    except Exception as e:
        frappe.throw(f"Unable to inspect DocType '{doctype}': {e}")

    fields = []
    for field in meta.fields:
        fields.append({
            "fieldname": field.fieldname,
            "label": field.label,
            "fieldtype": field.fieldtype,
            "options": field.options,
            "reqd": bool(field.reqd),
            "unique": bool(field.unique),
            "read_only": bool(field.read_only),
            "hidden": bool(field.hidden),
            "in_list_view": bool(field.in_list_view),
            "in_standard_filter": bool(field.in_standard_filter),
            "description": field.description,
        })

    return {
        "doctype": meta.name,
        "field_count": len(fields),
        "module": meta.module,
        "naming_rule": getattr(meta, "autoname", None),
        "is_submittable": bool(meta.is_submittable),
        "is_child_table": bool(meta.istable),
        "is_single": bool(meta.issingle),
        "is_virtual": bool(getattr(meta, "is_virtual", False)),
        "track_changes": bool(getattr(meta, "track_changes", False)),
        "fields": fields,
    }


def search_code(query, app=None, max_results=40):
    """Search application source code using ripgrep or a Python fallback."""
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

    max_results = max(1, min(int(max_results or 40), 200))

    # Preferred search implementation: ripgrep.
    cmd = [
        "rg",
        "-n",
        "--hidden",
        "--glob", "!.git",
        "--glob", "!node_modules",
        "--glob", "!*.map",
        "--max-count", str(max_results),
        query,
        str(root),
    ]

    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
        )

        if p.returncode in (0, 1):
            lines = p.stdout.splitlines()
            return "\n".join(lines[:max_results])[:MAX_OUTPUT]

    except FileNotFoundError:
        pass
    except subprocess.TimeoutExpired:
        return "Search timed out"

    # Portable fallback when ripgrep is unavailable.
    import re

    try:
        pattern = re.compile(query, re.IGNORECASE)
    except re.error:
        pattern = re.compile(re.escape(query), re.IGNORECASE)

    results = []

    allowed_extensions = {
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".json",
        ".html",
        ".css",
        ".scss",
        ".md",
        ".txt",
        ".xml",
        ".yaml",
        ".yml",
    }

    excluded_dirs = {
        ".git",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        "dist",
        "build",
    }

    try:
        for path in root.rglob("*"):
            if len(results) >= max_results:
                break

            if not path.is_file():
                continue

            if path.suffix.lower() not in allowed_extensions:
                continue

            if any(part in excluded_dirs for part in path.parts):
                continue

            try:
                content = path.read_text(
                    encoding="utf-8",
                    errors="replace",
                )
            except OSError:
                continue

            for line_number, line in enumerate(content.splitlines(), 1):
                if pattern.search(line):
                    results.append(
                        f"{path}:{line_number}:{line}"
                    )

                    if len(results) >= max_results:
                        break

    except OSError as exc:
        frappe.throw(f"Code search failed: {exc}")

    if not results:
        return "No matches found"

    return "\n".join(results)[:MAX_OUTPUT]


def read_file(path, start_line=None, end_line=None):
    """Read a source file, optionally restricted to a line range."""
    p = safe_path(path, [apps_root(), workspace_root()])

    if not p.is_file():
        frappe.throw("File not found")

    if p.stat().st_size > MAX_FILE_BYTES:
        frappe.throw("File is too large; search or request a smaller file")

    try:
        content = p.read_text(
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        frappe.throw(f"Unable to read file: {exc}")

    lines = content.splitlines()

    if start_line is None and end_line is None:
        return content

    try:
        start = int(start_line) if start_line is not None else 1
        end = int(end_line) if end_line is not None else len(lines)
    except (TypeError, ValueError):
        frappe.throw("start_line and end_line must be integers")

    if start < 1:
        frappe.throw("start_line must be greater than or equal to 1")

    if end < start:
        frappe.throw("end_line must be greater than or equal to start_line")

    start = min(start, len(lines) if lines else 1)
    end = min(end, len(lines))

    selected = lines[start - 1:end]

    return "\n".join(
        f"{line_number}: {line}"
        for line_number, line in enumerate(
            selected,
            start=start,
        )
    )


def write_generated_file(path, content):
    p = safe_path(path, [apps_root(), workspace_root()])

    # Never allow generated files to modify Frappe site configuration.
    # This protects sites/apps.txt, site_config.json,
    # common_site_config.json, and all other files under sites/.
    sites_root = (bench_root() / "sites").resolve()

    try:
        p.relative_to(sites_root)
        frappe.throw(
            "AI Engineer write_file cannot modify files under the "
            "Frappe sites directory"
        )
    except ValueError:
        pass

    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        frappe.throw("Generated file exceeds size limit")

    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")

    return str(p)


def create_app_skeleton(app_name, title=None, description=None):
    """Create a complete, importable Frappe application foundation."""

    if not SAFE_NAME.match(app_name):
        frappe.throw("App name must be lowercase snake_case, 2-40 chars")

    target = safe_path(apps_root() / app_name, [apps_root()])

    if target.exists():
        frappe.throw("App already exists")

    # Canonical application/module identity is derived exclusively
    # from the validated app_name. The model must not be able to
    # inject arbitrary prose into modules.txt, module_def.py,
    # Workspace metadata, or DocType module names.
    title = app_name.replace("_", " ").title()

    description = (
        description.strip()
        if isinstance(description, str) and description.strip()
        else "AI-generated Frappe application"
    )

    # Keep generated metadata bounded and safe.
    if len(description) > 500:
        description = description[:500].rstrip()

    # Serialize arbitrary model-provided text as a valid Python string
    # literal before embedding it into generated Python source.
    # This prevents quotes, backslashes, and newlines from corrupting
    # setup.py or hooks.py.
    description_literal = json.dumps(
        description,
        ensure_ascii=False,
    )

    title_literal = json.dumps(
        title,
        ensure_ascii=False,
    )

    # Frappe application package and its module package.
    app_pkg = target / app_name
    module_pkg = app_pkg / app_name

    app_pkg.mkdir(parents=True, exist_ok=True)
    module_pkg.mkdir(parents=True, exist_ok=True)

    for directory in (
        module_pkg / "doctype",
        module_pkg / "report",
        module_pkg / "page",
        module_pkg / "workspace",
        module_pkg / "dashboard",
        module_pkg / "dashboard_chart",
        module_pkg / "web_template",
        module_pkg / "public" / "js",
        module_pkg / "public" / "css",
        module_pkg / "templates",
    ):
        directory.mkdir(parents=True, exist_ok=True)

    files = {
        # Application packaging.
        target / "setup.py": (
            "from setuptools import setup, find_packages\n\n"
            "setup(\n"
            f'    name="{app_name}",\n'
            '    version="0.1.0",\n'
            f"    description={description_literal},\n"
            f"    author={title_literal},\n"
            '    author_email="dev@example.com",\n'
            '    license="MIT",\n'
            '    packages=find_packages(),\n'
            '    include_package_data=True,\n'
            '    zip_safe=False,\n'
            ')\n'
        ),

        target / "__init__.py": "",

        # Frappe application package.
        app_pkg / "__init__.py": '__version__ = "0.1.0"\n',

        app_pkg / "hooks.py": (
            f'app_name = "{app_name}"\n'
            f"app_title = {title_literal}\n"
            f"app_publisher = {title_literal}\n"
            f"app_description = {description_literal}\n"
            'app_email = "dev@example.com"\n'
            'app_license = "MIT"\n'
        ),

        # Canonical Frappe module name.
        # Generated DocTypes and Workspaces must use this exact value.
        app_pkg / "modules.txt": f"{title}\n",
        app_pkg / "patches.txt": "",
        app_pkg / "requirements.txt": "",

        # Frappe module package.
        module_pkg / "__init__.py": "",

        module_pkg / "module_def.py": (
            "from frappe import _\n\n\n"
            "def get_data():\n"
            "    return [\n"
            "        {\n"
            f'            "module_name": "{title}",\n'
            '            "color": "blue",\n'
            '            "icon": "tool",\n'
            '            "type": "module",\n'
            "        }\n"
            "    ]\n"
        ),

        # Standard Workspace for the generated Frappe module.
        # The Workspace uses exactly the same canonical module name.
        module_pkg / "workspace" / f"{app_name}.json": json.dumps(
            {
                "doctype": "Workspace",
                "name": title,
                "module": title,
                "label": title,
                "title": title,
                "public": 1,
                "is_standard": 1,
                "icon": "tool",
                "content": json.dumps(
                    [
                        {
                            "type": "header",
                            "data": {
                                "text": title,
                                "col": 12,
                            },
                        },
                        {
                            "type": "paragraph",
                            "data": {
                                "text": (
                                    f"Workspace for the {title} module."
                                ),
                                "col": 12,
                            },
                        },
                    ]
                ),
                "links": [],
            },
            indent=2,
        ),
    }

    for file_path, content in files.items():
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")

    # IMPORTANT:
    # Do not modify sites/apps.txt here.
    # Installation must happen only after validation through the
    # controlled AI Engineer lifecycle.

    return {
        "app": app_name,
        "title": title,
        "path": str(target),
        "module_path": str(module_pkg),
        "files": [
            str(file_path.relative_to(target))
            for file_path in files
        ],
        "structure": {
            "app_root": str(target),
            "python_package": str(app_pkg),
            "frappe_module": str(module_pkg),
            "doctype_path": str(module_pkg / "doctype"),
            "workspace_path": str(module_pkg / "workspace"),
        },
        "valid_foundation": True,
    }


def register_app(app_name):
    """
    Register a generated Frappe application with the current Bench.

    Registration is deliberately a dedicated controlled operation.
    The model supplies only the application name.

    Bench's official bench.app.install_app API is executed in a
    controlled subprocess because the Frappe worker Python path
    does not necessarily expose the Bench package.

    This does NOT install the app into a Frappe site/database.
    Site installation happens separately through install_app().
    """
    if not SAFE_NAME.match(app_name):
        frappe.throw("Invalid app name")

    app_path = safe_path(
        apps_root() / app_name,
        [apps_root()],
    )

    if not app_path.is_dir():
        frappe.throw(
            f"Application '{app_name}' does not exist under apps/"
        )

    # Validate filesystem structure before registration.
    validation = validate_generated_app(
        app_name,
        require_import=False,
    )

    if not validation.get("valid"):
        frappe.throw(
            "Application failed structural validation and cannot be "
            "registered: "
            + json.dumps(validation, default=str)
        )

    bench_root_path = bench_root()

    # The Bench Python package is installed separately from the
    # Frappe application environment. Explicitly expose it to
    # the controlled subprocess.
    bench_package_root = Path("/home/frappe/.bench")

    if not (bench_package_root / "bench").is_dir():
        frappe.throw(
            f"Bench Python package was not found at "
            f"{bench_package_root / 'bench'}"
        )

    env = os.environ.copy()

    existing_pythonpath = env.get("PYTHONPATH", "")

    python_paths = [str(bench_package_root)]

    if existing_pythonpath:
        python_paths.append(existing_pythonpath)

    env["PYTHONPATH"] = os.pathsep.join(python_paths)

    # Fixed trusted registration operation.
    #
    # The model supplies ONLY app_name.
    # No arbitrary shell command, pip argument, or filesystem
    # path can be supplied by the model.
    registration_script = r'''
import logging
import sys

import bench.app
from bench.app import install_app

# Bench 5.31.0 uses logger.log(message) in several places,
# while Python's logging.Logger.log() requires (level, message).
# Provide a subprocess-local compatibility wrapper without
# modifying the Bench installation itself.
original_logger_log = bench.app.logger.log

def compatible_logger_log(*args, **kwargs):
    if len(args) == 1 and not kwargs:
        return original_logger_log(logging.INFO, args[0])
    return original_logger_log(*args, **kwargs)

bench.app.logger.log = compatible_logger_log

app_name = sys.argv[1]
bench_path = sys.argv[2]

install_app(
    app_name,
    bench_path=bench_path,
    verbose=False,
    no_cache=False,
    restart_bench=False,
    skip_assets=False,
)
'''

    try:
        result = subprocess.run(
            [
                bench_python(),
                "-c",
                registration_script,
                app_name,
                str(bench_root_path),
            ],
            cwd=str(bench_root_path),
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )

    except subprocess.TimeoutExpired:
        message = (
            f"Bench application registration timed out for '{app_name}'"
        )

        try:
            frappe.log_error(message, "AI Engineer register_app timeout")
        except Exception:
            pass

        raise RuntimeError(message)

    except Exception as exc:
        message = (
            "Bench application registration could not be started: "
            f"{type(exc).__name__}: {exc}"
        )

        try:
            frappe.log_error(
                frappe.get_traceback(),
                "AI Engineer register_app subprocess failed",
            )
        except Exception:
            pass

        raise RuntimeError(message) from exc

    if result.returncode != 0:
        output = (
            (result.stderr or "").strip()
            or (result.stdout or "").strip()
            or "Unknown Bench registration error"
        )

        try:
            frappe.log_error(
                output,
                "AI Engineer Bench application registration failed",
            )
        except Exception:
            pass

        raise RuntimeError(
            "Bench application registration failed: "
            + output
        )

    # Verify import using the Bench Python interpreter.
    #
    # AI Engineer's worker process may use a different Python
    # environment, so import validation must not happen through
    # importlib in the current worker process.
    import_script = r"""
import importlib
import sys

app_name = sys.argv[1]

importlib.invalidate_caches()

module = importlib.import_module(app_name)

module_file = getattr(module, "__file__", None)

if not module_file:
    raise RuntimeError(
        f"Application '{app_name}' imported without a valid module file"
    )

print(module_file)
"""

    try:
        import_result = subprocess.run(
            [
                bench_python(),
                "-c",
                import_script,
                app_name,
            ],
            cwd=str(bench_root_path),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"Bench Python import validation timed out for '{app_name}'"
        )

    if import_result.returncode != 0:
        output = (
            (import_result.stderr or "").strip()
            or (import_result.stdout or "").strip()
            or "Unknown Python import error"
        )

        frappe.throw(
            f"Application '{app_name}' was registered by Bench but "
            f"cannot be imported by Bench Python: {output}"
        )

    module_file = (import_result.stdout or "").strip()

    if not module_file:
        frappe.throw(
            f"Application '{app_name}' was registered but Bench Python "
            f"did not return a module file"
        )

    # Final validation after Bench registration.
    final_validation = validate_generated_app(
        app_name,
        require_import=True,
    )

    if not final_validation.get("valid"):
        frappe.throw(
            "Application registered with Bench but failed "
            "post-registration validation: "
            + json.dumps(final_validation, default=str)
        )

    return {
        "app": app_name,
        "registered": True,
        "path": str(app_path),
        "module": module_file,
        "validation": final_validation,
        "bench_stdout": (result.stdout or "").strip(),
    }



def validate_generated_app(app_name, require_import=False):
    """
    Validate structural integrity and optionally importability
    of a generated Frappe application.
    """
    if not SAFE_NAME.match(app_name):
        return {"valid": False, "error": "Invalid app name"}

    target = safe_path(apps_root() / app_name, [apps_root()])

    if not target.is_dir():
        return {"valid": False, "error": "App directory does not exist"}

    setup_py = target / "setup.py"
    init_py = target / "__init__.py"
    app_pkg = target / app_name
    app_init_py = app_pkg / "__init__.py"
    hooks_py = app_pkg / "hooks.py"
    modules_txt = app_pkg / "modules.txt"
    patches_txt = app_pkg / "patches.txt"
    requirements_txt = app_pkg / "requirements.txt"
    module_pkg = app_pkg / app_name
    module_init_py = module_pkg / "__init__.py"
    module_def_py = module_pkg / "module_def.py"

    required_files = [
        setup_py,
        init_py,
        app_init_py,
        hooks_py,
        modules_txt,
        patches_txt,
        requirements_txt,
        module_init_py,
        module_def_py,
    ]

    for p in required_files:
        if not p.is_file():
            return {"valid": False, "error": f"Missing required file: {p.name}"}

    workspace_dir = module_pkg / "workspace"
    if not workspace_dir.is_dir():
        return {
            "valid": False,
            "error": "Missing module workspace directory",
        }

    workspace_files = list(workspace_dir.glob("*.json"))
    if not workspace_files:
        return {
            "valid": False,
            "error": "Missing Workspace JSON file under module workspace",
        }

    if not require_import:
        return {
            "valid": True,
            "structure_valid": True,
            "import_valid": None,
        }

    bench_root_path = bench_root()
    bench_package_root = Path("/home/frappe/.bench")

    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "")
    python_paths = [str(bench_package_root), str(apps_root())]

    if existing_pythonpath:
        python_paths.append(existing_pythonpath)

    env["PYTHONPATH"] = os.pathsep.join(python_paths)

    import_script = r'''
import importlib
import sys

app_name = sys.argv[1]
apps_root = sys.argv[2]

if apps_root not in sys.path:
    sys.path.insert(0, apps_root)

importlib.invalidate_caches()

try:
    app_module = importlib.import_module(app_name)
    module_pkg = importlib.import_module(f"{app_name}.{app_name}")
    hooks = importlib.import_module(f"{app_name}.hooks")
    module_def = importlib.import_module(f"{app_name}.{app_name}.module_def")
except Exception as e:
    print(f"IMPORT_ERROR: {e}")
    sys.exit(1)

print("IMPORT_OK")
'''

    try:
        res = subprocess.run(
            [
                bench_python(),
                "-c",
                import_script,
                app_name,
                str(apps_root()),
            ],
            cwd=str(bench_root_path),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"valid": False, "error": "Import validation timed out"}

    if res.returncode != 0:
        err = (
            (res.stderr or "").strip()
            or (res.stdout or "").strip()
            or "Unknown import error"
        )
        return {"valid": False, "error": f"Import check failed: {err}"}

    return {
        "valid": True,
        "structure_valid": True,
        "import_valid": True,
    }
def install_app(app_name):
    """
    Execute the complete validated application lifecycle on the
    administrator-configured site.

    Lifecycle:
      1. structural validation
      2. Bench/Python import validation
      3. Bench site installation
      4. database migration
      5. cache clearing
      6. final application verification

    The model supplies only the application name.
    The target site comes exclusively from AI Engineer Settings.
    """
    if not SAFE_NAME.match(app_name):
        frappe.throw("Invalid app name")

    configured_site = get_configured_site()

    app_path = safe_path(
        apps_root() / app_name,
        [apps_root()],
    )

    if not app_path.is_dir():
        frappe.throw(
            f"Application '{app_name}' does not exist under apps/"
        )

    # --------------------------------------------------------
    # Pre-install validation
    # --------------------------------------------------------

    validation = validate_generated_app(
        app_name,
        require_import=True,
    )

    if not validation.get("valid"):
        frappe.throw(
            "Application failed validation and cannot be installed: "
            + json.dumps(validation, default=str)
        )

    # --------------------------------------------------------
    # Install application
    # --------------------------------------------------------

    install_command = (
        f"bench --site {configured_site} "
        f"install-app {app_name}"
    )

    install_result = run_allowed(
        install_command,
        bench_root(),
    )

    if install_result.get("returncode") != 0:
        frappe.throw(
            "Application installation failed: "
            + json.dumps(install_result, default=str)
        )

    # --------------------------------------------------------
    # Database migration
    # --------------------------------------------------------

    migrate_command = (
        f"bench --site {configured_site} migrate"
    )

    migrate_result = run_allowed(
        migrate_command,
        bench_root(),
    )

    if migrate_result.get("returncode") != 0:
        frappe.throw(
            "Application migration failed after installation: "
            + json.dumps(migrate_result, default=str)
        )

    # --------------------------------------------------------
    # Clear cache
    # --------------------------------------------------------

    clear_cache_command = (
        f"bench --site {configured_site} clear-cache"
    )

    clear_cache_result = run_allowed(
        clear_cache_command,
        bench_root(),
    )

    if clear_cache_result.get("returncode") != 0:
        frappe.throw(
            "Application cache clearing failed after installation: "
            + json.dumps(clear_cache_result, default=str)
        )

    # --------------------------------------------------------
    # Final verification
    # --------------------------------------------------------

    verification = verify_app(app_name)

    if not verification.get("verified"):
        frappe.throw(
            "Application lifecycle completed its installation steps "
            "but final verification failed: "
            + json.dumps(verification, default=str)
        )

    return {
        "app": app_name,
        "site": configured_site,
        "installed": True,
        "migrated": True,
        "cache_cleared": True,
        "verified": True,
        "commands": {
            "install": install_command,
            "migrate": migrate_command,
            "clear_cache": clear_cache_command,
        },
        "installation_output": install_result,
        "migration_output": migrate_result,
        "clear_cache_output": clear_cache_result,
        "verification": verification,
    }



def verify_app(app_name):
    """
    Verify the complete application lifecycle against the exact
    administrator-configured site.

    Verification is intentionally performed through the Bench Python
    environment with an explicit Frappe site context. There is no
    fallback to frappe.local.site.
    """
    if not SAFE_NAME.match(app_name):
        frappe.throw("Invalid app name")

    configured_site = get_configured_site()

    app_path = safe_path(
        apps_root() / app_name,
        [apps_root()],
    )

    if not app_path.is_dir():
        frappe.throw(
            f"Application '{app_name}' does not exist under apps/"
        )

    expected_module = app_name.replace("_", " ").title()

    # --------------------------------------------------------
    # Bench registration
    # --------------------------------------------------------

    apps_txt = bench_root() / "sites" / "apps.txt"

    if not apps_txt.is_file():
        frappe.throw(
            "Bench sites/apps.txt was not found"
        )

    registered_apps = [
        line.strip()
        for line in apps_txt.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]

    registered = app_name in registered_apps

    if not registered:
        return {
            "verified": False,
            "app": app_name,
            "site": configured_site,
            "registered": False,
            "imported": False,
            "installed": False,
            "reason": "Application is not registered with Bench",
        }

    # --------------------------------------------------------
    # Bench Python import verification
    # --------------------------------------------------------

    bench_package_root = Path("/home/frappe/.bench")
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        str(bench_package_root)
        if not existing_pythonpath
        else f"{bench_package_root}:{existing_pythonpath}"
    )

    import_script = r"""
import importlib
import sys

app_name = sys.argv[1]

importlib.invalidate_caches()

module = importlib.import_module(app_name)

module_file = getattr(module, "__file__", None)

if not module_file:
    raise RuntimeError(
        f"Application '{app_name}' imported without a valid module file"
    )

print(module_file)
"""

    try:
        import_result = subprocess.run(
            [
                bench_python(),
                "-c",
                import_script,
                app_name,
            ],
            cwd=str(bench_root()),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

        imported = (
            import_result.returncode == 0
            and bool((import_result.stdout or "").strip())
        )

    except subprocess.TimeoutExpired:
        return {
            "verified": False,
            "app": app_name,
            "site": configured_site,
            "registered": True,
            "imported": False,
            "installed": False,
            "reason": (
                f"Bench Python import verification timed out "
                f"for '{app_name}'"
            ),
        }

    except Exception as exc:
        return {
            "verified": False,
            "app": app_name,
            "site": configured_site,
            "registered": True,
            "imported": False,
            "installed": False,
            "reason": (
                f"Bench Python import verification failed: "
                f"{type(exc).__name__}: {exc}"
            ),
        }

    if not imported:
        output = (
            (import_result.stderr or "").strip()
            or (import_result.stdout or "").strip()
            or "Unknown Python import error"
        )

        return {
            "verified": False,
            "app": app_name,
            "site": configured_site,
            "registered": True,
            "imported": False,
            "installed": False,
            "reason": (
                f"Application cannot be imported by Bench Python: "
                f"{output}"
            ),
        }

    # --------------------------------------------------------
    # Explicit configured-site verification
    # --------------------------------------------------------
    #
    # IMPORTANT:
    # Do not use the current AI Engineer request context here.
    # The verification target is always the Site Name configured
    # in AI Engineer Settings.
    #

    site_verify_script = r"""
import json
import sys
from pathlib import Path

import frappe

site = sys.argv[1]
app_name = sys.argv[2]
bench_root = Path(sys.argv[3])

expected_module = app_name.replace("_", " ").title()

frappe.init(site=site)
frappe.connect()

try:
    installed_apps = frappe.get_installed_apps()
    installed = app_name in installed_apps

    module_defs = frappe.get_all(
        "Module Def",
        filters={
            "app_name": app_name,
        },
        fields=[
            "name",
            "module_name",
            "app_name",
        ],
    )

    canonical_module_defs = [
        item
        for item in module_defs
        if item.get("module_name") == expected_module
    ]

    app_path = (
        bench_root
        / "apps"
        / app_name
        / app_name
        / app_name
    )

    doctype_root = app_path / "doctype"

    doctype_checks = []

    if doctype_root.is_dir():
        for json_path in sorted(doctype_root.rglob("*.json")):
            try:
                data = json.loads(
                    json_path.read_text(
                        encoding="utf-8"
                    )
                )
            except Exception:
                continue

            if data.get("doctype") != "DocType":
                continue

            doctype_name = data.get("name")

            if not doctype_name:
                continue

            exists = bool(
                frappe.db.exists(
                    "DocType",
                    doctype_name,
                )
            )

            db_module = None

            if exists:
                db_module = frappe.db.get_value(
                    "DocType",
                    doctype_name,
                    "module",
                )

            doctype_checks.append({
                "doctype": doctype_name,
                "exists": exists,
                "expected_module": expected_module,
                "module": db_module,
                "module_verified": (
                    exists
                    and db_module == expected_module
                ),
                "path": str(json_path),
            })

    workspace_root = app_path / "workspace"

    workspace_checks = []

    if workspace_root.is_dir():
        for json_path in sorted(workspace_root.rglob("*.json")):
            try:
                data = json.loads(
                    json_path.read_text(
                        encoding="utf-8"
                    )
                )
            except Exception:
                continue

            if data.get("doctype") != "Workspace":
                continue

            workspace_name = data.get("name")

            if not workspace_name:
                continue

            exists = bool(
                frappe.db.exists(
                    "Workspace",
                    workspace_name,
                )
            )

            db_module = None
            db_public = None
            db_is_standard = None

            if exists:
                workspace_doc = frappe.get_doc(
                    "Workspace",
                    workspace_name,
                )

                db_module = workspace_doc.module
                db_public = workspace_doc.public
                db_is_standard = workspace_doc.is_standard

            workspace_checks.append({
                "workspace": workspace_name,
                "exists": exists,
                "expected_name": expected_module,
                "expected_module": expected_module,
                "module": db_module,
                "public": db_public,
                "is_standard": db_is_standard,
                "metadata_verified": (
                    exists
                    and workspace_name == expected_module
                    and db_module == expected_module
                    and int(db_public or 0) == 1
                    and int(db_is_standard or 0) == 1
                ),
                "path": str(json_path),
            })

    module_defs_verified = bool(
        canonical_module_defs
    )

    doctypes_verified = bool(
        doctype_checks
    ) and all(
        item["exists"]
        and item["module_verified"]
        for item in doctype_checks
    )

    workspace_required = bool(
        doctype_checks
    )

    workspace_verified = (
        not workspace_required
        or (
            len(workspace_checks) == 1
            and all(
                item["exists"]
                and item["metadata_verified"]
                for item in workspace_checks
            )
        )
    )

    verified = (
        installed
        and module_defs_verified
        and doctypes_verified
        and workspace_verified
    )

    print(
        json.dumps(
            {
                "site": site,
                "app": app_name,
                "installed": installed,
                "installed_apps": installed_apps,
                "module_defs": module_defs,
                "canonical_module": expected_module,
                "module_defs_verified": module_defs_verified,
                "doctype_checks": doctype_checks,
                "doctypes_verified": doctypes_verified,
                "workspace_checks": workspace_checks,
                "workspace_required": workspace_required,
                "workspace_verified": workspace_verified,
                "verified": verified,
            },
            default=str,
        )
    )

finally:
    frappe.destroy()
"""

    try:
        site_result = subprocess.run(
            [
                bench_python(),
                "-c",
                site_verify_script,
                configured_site,
                app_name,
                str(bench_root()),
            ],
            cwd=str(bench_root()),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

    except subprocess.TimeoutExpired:
        return {
            "verified": False,
            "app": app_name,
            "site": configured_site,
            "registered": True,
            "imported": imported,
            "installed": False,
            "reason": (
                f"Configured-site verification timed out "
                f"for '{configured_site}'"
            ),
        }

    except Exception as exc:
        return {
            "verified": False,
            "app": app_name,
            "site": configured_site,
            "registered": True,
            "imported": imported,
            "installed": False,
            "reason": (
                f"Configured-site verification failed: "
                f"{type(exc).__name__}: {exc}"
            ),
        }

    if site_result.returncode != 0:
        output = (
            (site_result.stderr or "").strip()
            or (site_result.stdout or "").strip()
            or "Unknown configured-site verification error"
        )

        return {
            "verified": False,
            "app": app_name,
            "site": configured_site,
            "registered": True,
            "imported": imported,
            "installed": False,
            "reason": (
                "Configured-site verification failed: "
                + output
            ),
        }

    try:
        site_verification = json.loads(
            (site_result.stdout or "").strip()
        )
    except Exception as exc:
        return {
            "verified": False,
            "app": app_name,
            "site": configured_site,
            "registered": True,
            "imported": imported,
            "installed": False,
            "reason": (
                "Configured-site verification returned invalid JSON: "
                f"{type(exc).__name__}: {exc}"
            ),
            "raw_output": (
                site_result.stdout or ""
            ).strip(),
        }

    installed = bool(
        site_verification.get("installed")
    )

    module_defs_verified = bool(
        site_verification.get(
            "module_defs_verified"
        )
    )

    doctypes_verified = bool(
        site_verification.get(
            "doctypes_verified"
        )
    )

    workspace_verified = bool(
        site_verification.get(
            "workspace_verified"
        )
    )

    verified = bool(
        registered
        and imported
        and installed
        and module_defs_verified
        and doctypes_verified
        and workspace_verified
        and site_verification.get("verified")
    )

    return {
        "verified": verified,
        "app": app_name,
        "site": configured_site,
        "registered": registered,
        "imported": imported,
        "installed": installed,
        "installed_apps": site_verification.get(
            "installed_apps",
            [],
        ),
        "canonical_module": expected_module,
        "module_defs": site_verification.get(
            "module_defs",
            [],
        ),
        "module_defs_verified": module_defs_verified,
        "doctype_checks": site_verification.get(
            "doctype_checks",
            [],
        ),
        "doctypes_verified": doctypes_verified,
        "workspace_checks": site_verification.get(
            "workspace_checks",
            [],
        ),
        "workspace_required": site_verification.get(
            "workspace_required",
            False,
        ),
        "workspace_verified": workspace_verified,
        "site_verification": site_verification,
    }


def validate_generated_app(app_name, require_import=True):
    """Validate generated Frappe app structure and Python files."""
    if not SAFE_NAME.match(app_name):
        frappe.throw("Invalid app name")

    target = safe_path(apps_root() / app_name, [apps_root()])

    if not target.exists():
        frappe.throw("App does not exist")

    # Standard generated Frappe application structure:
    #
    # apps/<app_name>/
    #     __init__.py
    #     setup.py
    #     <app_name>/
    #         __init__.py
    #         hooks.py
    #         modules.txt
    #         patches.txt
    #         requirements.txt
    #         <app_name>/
    #             __init__.py
    #             module_def.py
    #             doctype/
    #             workspace/
    #
    # The middle package is the Frappe application package.
    # The inner package is the Frappe module referenced by modules.txt.
    app_pkg = target / app_name
    module_pkg = app_pkg / app_name

    errors = []
    warnings = []

    required_files = [
        app_pkg / "hooks.py",
        app_pkg / "modules.txt",
        app_pkg / "patches.txt",
        app_pkg / "__init__.py",
        module_pkg / "__init__.py",
        module_pkg / "module_def.py",
    ]

    for path in required_files:
        if not path.is_file():
            errors.append(f"Missing required file: {path}")

    required_dirs = [
        module_pkg / "doctype",
        module_pkg / "workspace",
    ]

    for path in required_dirs:
        if not path.is_dir():
            errors.append(f"Missing required directory: {path}")

    # A newly generated application may not be importable until
    # Bench has registered/installed it into the Python environment.
    #
    # AI Engineer workers may use a different Python interpreter from
    # the Bench virtual environment. Therefore import validation must
    # always use the Bench Python interpreter.
    if require_import:
        bench_package_root = Path("/home/frappe/.bench")
        env = os.environ.copy()
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            str(bench_package_root)
            if not existing_pythonpath
            else f"{bench_package_root}:{existing_pythonpath}"
        )

        import_script = r"""
import importlib
import sys

app_name = sys.argv[1]

importlib.invalidate_caches()

module = importlib.import_module(app_name)

module_file = getattr(module, "__file__", None)

if not module_file:
    raise RuntimeError(
        f"Python module {app_name} imported without __file__"
    )

print(module_file)
"""

        try:
            import_result = subprocess.run(
                [
                    bench_python(),
                    "-c",
                    import_script,
                    app_name,
                ],
                cwd=str(bench_root()),
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )

            if import_result.returncode != 0:
                output = (
                    (import_result.stderr or "").strip()
                    or (import_result.stdout or "").strip()
                    or "Unknown Python import error"
                )

                errors.append(
                    f"Cannot import {app_name} using Bench Python: "
                    f"{output}"
                )

            elif not (import_result.stdout or "").strip():
                errors.append(
                    f"Python module {app_name} imported without "
                    f"returning a module file"
                )

        except subprocess.TimeoutExpired:
            errors.append(
                f"Bench Python import validation timed out for {app_name}"
            )

        except Exception as e:
            errors.append(
                f"Bench Python import validation failed for "
                f"{app_name}: {type(e).__name__}: {e}"
            )

    # Compile every Python file.
    py_files = list(target.rglob("*.py"))

    for path in py_files:
        try:
            compile(
                path.read_text(encoding="utf-8"),
                str(path),
                "exec",
            )
        except SyntaxError as e:
            errors.append(f"{path}: {e}")

    # Validate generated metadata strictly.
    import json

    # --------------------------------------------------------
    # Canonical module identity
    # --------------------------------------------------------
    #
    # The canonical Frappe module is deterministic:
    #
    #     gym_membership -> Gym Membership
    #
    # It must never come from arbitrary model-generated prose.
    # --------------------------------------------------------

    expected_module = app_name.replace("_", " ").title()

    modules_file = app_pkg / "modules.txt"

    canonical_modules = []

    if modules_file.is_file():
        canonical_modules = [
            line.strip()
            for line in modules_file.read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip()
        ]

    if canonical_modules != [expected_module]:
        errors.append(
            f"{modules_file}: expected exactly one canonical module "
            f"'{expected_module}', got {canonical_modules!r}"
        )

    canonical_module = (
        canonical_modules[0]
        if canonical_modules == [expected_module]
        else None
    )

    # --------------------------------------------------------
    # module_def.py
    # --------------------------------------------------------

    module_def_file = module_pkg / "module_def.py"

    if module_def_file.is_file():
        module_def_text = module_def_file.read_text(
            encoding="utf-8"
        )

        expected_module_literal = (
            f'"module_name": "{expected_module}"'
        )

        if expected_module_literal not in module_def_text:
            errors.append(
                f"{module_def_file}: module definition does not "
                f"declare canonical module '{expected_module}'"
            )

    # --------------------------------------------------------
    # DocType JSON
    # --------------------------------------------------------

    doctype_root = module_pkg / "doctype"

    doctype_files = (
        list(doctype_root.rglob("*.json"))
        if doctype_root.is_dir()
        else []
    )

    valid_doctype_files = []

    for path in doctype_files:
        try:
            data = json.loads(
                path.read_text(encoding="utf-8")
            )

            if data.get("doctype") != "DocType":
                errors.append(
                    f"{path}: JSON under doctype must declare "
                    f'doctype="DocType"'
                )
                continue

            if not data.get("name"):
                errors.append(
                    f"{path}: DocType JSON has no explicit name"
                )
                continue

            if not data.get("module"):
                errors.append(
                    f"{path}: DocType JSON has no module"
                )
                continue

            if canonical_module and data.get("module") != canonical_module:
                errors.append(
                    f"{path}: DocType module '{data.get('module')}' "
                    f"does not match canonical module "
                    f"'{canonical_module}'"
                )
                continue

            valid_doctype_files.append(path)

        except Exception as exc:
            errors.append(
                f"{path}: invalid JSON: "
                f"{type(exc).__name__}: {exc}"
            )

    # --------------------------------------------------------
    # Workspace JSON
    # --------------------------------------------------------
    #
    # Exactly one standard Workspace is required whenever the
    # application contains generated DocTypes.
    #
    # The canonical path is:
    #
    # <module>/workspace/<app_name>.json
    # --------------------------------------------------------

    workspace_root = module_pkg / "workspace"

    workspace_files = (
        list(workspace_root.rglob("*.json"))
        if workspace_root.is_dir()
        else []
    )

    expected_workspace_path = (
        workspace_root / f"{app_name}.json"
    )

    if valid_doctype_files:
        if len(workspace_files) != 1:
            errors.append(
                f"{workspace_root}: expected exactly one Workspace "
                f"JSON for an application containing DocTypes; "
                f"found {len(workspace_files)}"
            )

        if not expected_workspace_path.is_file():
            errors.append(
                f"{expected_workspace_path}: canonical Workspace "
                f"file is missing"
            )

    valid_workspace_files = []

    for path in workspace_files:
        try:
            data = json.loads(
                path.read_text(encoding="utf-8")
            )

            if data.get("doctype") != "Workspace":
                errors.append(
                    f"{path}: Workspace JSON must declare "
                    f'doctype="Workspace"'
                )
                continue

            if not data.get("name"):
                errors.append(
                    f"{path}: Workspace has no name"
                )
                continue

            if not data.get("module"):
                errors.append(
                    f"{path}: Workspace has no module"
                )
                continue

            if canonical_module and data.get("module") != canonical_module:
                errors.append(
                    f"{path}: Workspace module "
                    f"'{data.get('module')}' does not match "
                    f"canonical module '{canonical_module}'"
                )

            if data.get("name") != expected_module:
                errors.append(
                    f"{path}: Workspace name '{data.get('name')}' "
                    f"does not match canonical module "
                    f"'{expected_module}'"
                )

            if data.get("module") != expected_module:
                errors.append(
                    f"{path}: Workspace module '{data.get('module')}' "
                    f"does not match expected module "
                    f"'{expected_module}'"
                )

            if data.get("public") != 1:
                errors.append(
                    f"{path}: Workspace must have public=1"
                )

            if data.get("is_standard") != 1:
                errors.append(
                    f"{path}: Workspace must have is_standard=1"
                )

            if path.resolve() != expected_workspace_path.resolve():
                errors.append(
                    f"{path}: Workspace must use canonical path "
                    f"{expected_workspace_path}"
                )

            valid_workspace_files.append(path)

        except Exception as exc:
            errors.append(
                f"{path}: invalid Workspace JSON: "
                f"{type(exc).__name__}: {exc}"
            )

    return {
        "app": app_name,
        "path": str(target),
        "files": len(py_files),
        "doctype_files": len(doctype_files),
        "workspace_files": len(workspace_files),
        "expected_module": expected_module,
        "canonical_module": canonical_module,
        "expected_workspace": str(expected_workspace_path),
        "syntax_errors": [
            error for error in errors
            if "SyntaxError" in error
        ],
        "errors": errors,
        "warnings": warnings,
        "valid": not errors,
    }

def run_allowed(command, cwd):
    """Run a narrowly allowlisted command required by the AI Engineer lifecycle.

    The model never receives unrestricted shell access. Commands are checked
    against both the administrator-configured allowlist and a hard safety
    policy for Frappe/bench lifecycle operations.

    Bench commands are restricted to the site explicitly configured in
    AI Engineer Settings. There is intentionally no automatic fallback to
    frappe.local.site or any other site.
    """
    s = settings()

    if not s.allow_code_execution:
        frappe.throw("Code execution is disabled in AI Engineer Settings")

    # AI Engineer must always operate on the site explicitly configured
    # by the administrator. Never automatically use frappe.local.site.
    configured_site = get_configured_site()

    command = (command or "").strip()

    if not command:
        frappe.throw("Command cannot be empty")

    # Never permit shell composition or redirection.
    forbidden_tokens = [
        ";", "&&", "||", "|", "`", "$(", ">", "<"
    ]
    if any(token in command for token in forbidden_tokens):
        frappe.throw("Shell operators are not allowed in AI Engineer commands")

    # Hard safety policy.
    # These are the only command families AI Engineer may execute.
    safe_prefixes = [
        "python -m compileall",
        "python -m pytest",
        "ruff check",
        "bench --version",
        "bench --site ",
    ]

    if not any(
        command == prefix.rstrip() or command.startswith(prefix)
        for prefix in safe_prefixes
    ):
        frappe.throw(
            "Command is not permitted by the AI Engineer safety policy"
        )

    # bench --site is further restricted to lifecycle commands only.
    if command.startswith("bench --site "):
        parts = command.split()

        # Expected:
        # bench --site <site> <command> [safe arguments]
        if len(parts) < 4:
            frappe.throw("Invalid bench command")

        site = parts[2]
        bench_action = parts[3]

        if not re.fullmatch(r"[a-zA-Z0-9_.-]+", site):
            frappe.throw("Invalid site name")

        # AI Engineer may operate ONLY on the site configured in Settings.
        if site != configured_site:
            frappe.throw(
                f"Command targets site '{site}', but AI Engineer is configured "
                f"to operate on site '{configured_site}'. "
                "Update AI Engineer Settings if a different site is required."
            )

        allowed_bench_actions = {
            "install-app",
            "migrate",
            "clear-cache",
            "clear-website-cache",
            "list-apps",
            "version",
        }

        if bench_action not in allowed_bench_actions:
            frappe.throw(
                f"bench action '{bench_action}' is not permitted by "
                "AI Engineer safety policy"
            )

        # install-app must contain a simple application name.
        if bench_action == "install-app":
            if len(parts) != 5:
                frappe.throw("install-app requires exactly one app name")

            app_name = parts[4]

            if not re.fullmatch(r"[a-z][a-z0-9_]{1,39}", app_name):
                frappe.throw("Invalid app name")

            # Prevent installing arbitrary system packages/apps.
            apps_dir = apps_root()
            app_path = apps_dir / app_name

            if not app_path.exists():
                frappe.throw(
                    f"Application '{app_name}' does not exist under apps/"
                )

        # No extra arguments for migration/cache/list operations.
        elif bench_action in {
            "migrate",
            "clear-cache",
            "clear-website-cache",
            "list-apps",
            "version",
        }:
            if len(parts) != 4:
                frappe.throw(
                    f"bench --site {bench_action} does not accept extra arguments"
                )

    # Check administrator-configured allowlist as an additional layer.
    configured = [
        x.strip()
        for x in (s.allowed_commands or "").splitlines()
        if x.strip()
    ]

    configured_ok = False

    for item in configured:
        # {site} is the only supported site placeholder in the
        # administrator-configured allowlist.
        if item.startswith("bench --site {site}"):
            item = item.replace(
                "bench --site {site}",
                f"bench --site {configured_site}",
                1,
            )

        if command == item or command.startswith(item + " "):
            configured_ok = True
            break

    if not configured_ok:
        frappe.throw(
            "Command is not in the configured AI Engineer allowlist"
        )

    cwd = safe_path(cwd, [bench_root(), apps_root(), workspace_root()])

    p = subprocess.run(
        command,
        cwd=cwd,
        shell=True,
        capture_output=True,
        text=True,
        timeout=120,
    )

    return {
        "returncode": p.returncode,
        "stdout": p.stdout[-MAX_OUTPUT:],
        "stderr": p.stderr[-MAX_OUTPUT:],
    }

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
{"action":"inspect_apps|inspect_doctype|search_code|read_file|create_app|write_file|validate_app|register_app|install_app|run_command|verify_app|finish","args":{...},"message":"brief reason"}
Allowed args:
inspect_apps: {}
inspect_doctype: {"doctype":"DocType Name"}
search_code: {"query":"...","app":"optional"}
read_file: {"path":"absolute path","start_line":"optional line number","end_line":"optional line number"}
create_app: {"app_name":"snake_case","title":"...","description":"..."}
write_file: {"path":"absolute path inside generated app/workspace","content":"..."}
validate_app: {"app_name":"..."}
register_app: {"app_name":"..."}
install_app: {"app_name":"..."}
run_command: {"command":"exact allowlisted command","cwd":"absolute path"}
verify_app: {"app_name":"..."}
finish: {"answer":"..."}

IMPORTANT:
- register_app is the ONLY normal mechanism for registering a generated app with the Bench environment.
- install_app is the ONLY normal mechanism for installing a generated app on the configured Frappe site.
- register_app accepts only the app name and internally uses Bench's official application installation API.
- install_app always uses the Site Name configured in AI Engineer Settings.
- Never provide or invent a site name for install_app.
- Never manually edit sites/apps.txt.
- Never use write_file to modify sites/apps.txt or any file under sites/.
- Never claim an app is installed merely because create_app succeeded.
- Never claim an app is installed merely because register_app succeeded.
- Never use pip install, pip install -e, bench get-app, bench new-app, or arbitrary package installation as a model-controlled command.
- The model must not receive unrestricted shell or package-management access.

For app creation, first inspect relevant existing code. Then create the skeleton, write all required files, validate the application structure without requiring import, register the application through the controlled register_app action, validate again including Python importability, install the application through the controlled install_app action, synchronize the site when required, clear cache when necessary, verify the result, and only then report completion.

The normal lifecycle is:

1. inspect relevant existing apps/code
2. create the application structure
3. write all required Python/JSON/JS/CSS/metadata files
4. validate Python syntax, package structure, and Frappe metadata
5. register the application with Bench using the controlled register_app action
6. validate the application again, including Python importability
7. install the app using the controlled install_app action
8. run the appropriate configured-site migration when metadata/database synchronization is required
9. clear cache when necessary
10. verify the app using the configured site's installed-app list and database/metadata inspection
11. verify requested Modules, DocTypes, Workspaces, Pages, Reports, and other metadata
12. only then report the application as complete

The generated app must NOT be manually added to sites/apps.txt. register_app uses Bench's official application installation mechanism to synchronize the generated app with the Bench environment.

AI Engineer may execute the narrowly allowlisted Frappe lifecycle commands without asking the user to manually perform routine installation or migration steps. Do not ask the user to copy/paste ordinary bench installation commands merely because the command is a bench command.

Never report installation success based only on the create_app or validate_app result. Installation must have succeeded and verification must confirm that the app is actually installed.

Never execute destructive commands such as uninstall-app, drop-site, reinstall, drop-database, restore, rm, git reset, or arbitrary shell commands.

After installation/migration, verify that the generated Module Def, DocTypes, Workspaces, Pages, Reports, and other requested metadata actually exist in the database before declaring the application complete.

IMPORTANT CONTROLLED APP LIFECYCLE:
- create_app creates only the filesystem foundation.
- validate_app before registration validates structure and syntax without requiring import.
- register_app registers the generated application with the current Bench using Bench's official API.
- validate_app after registration verifies that the application is importable.
- install_app installs the app into the Site Name configured in AI Engineer Settings.
- run_command may perform only the explicitly allowlisted site migration/cache/verification commands.
- verify_app is required before reporting an application as complete.
- The model must never manually edit sites/apps.txt.
- The model must never use write_file against the sites directory.

IMPORTANT APP PATH RULE:
Frappe applications use an outer application directory and an inner
Python/application package containing the actual Frappe module.

For an app named "asset_tools", the generated structure is:

/workspace/development/frappe-bench/apps/asset_tools/
    asset_tools/
        hooks.py
        modules.txt
        patches.txt
        requirements.txt
        __init__.py
        asset_tools/
            __init__.py
            module_def.py
            doctype/
            workspace/
            report/
            page/
            dashboard/
            public/
            templates/

The repeated app name is intentional and required by the Frappe module
loader in this environment.

For example, for the "asset_tools" app, a DocType named
"Asset Tool Configuration" MUST be written under:

/workspace/development/frappe-bench/apps/asset_tools/asset_tools/asset_tools/doctype/asset_tool_configuration/

A Workspace named "Asset Tools" MUST be written under:

/workspace/development/frappe-bench/apps/asset_tools/asset_tools/asset_tools/workspace/asset_tools.json

Never write Frappe module metadata, DocTypes, Pages, Reports, Workspaces,
or other module-level artifacts directly under:

/workspace/development/frappe-bench/apps/asset_tools/asset_tools/doctype/

They belong inside the actual Frappe module package:

/workspace/development/frappe-bench/apps/asset_tools/asset_tools/asset_tools/

All write_file paths for generated app files must stay inside the existing
app root and must follow this structure.

After creating an app, always validate the structure before declaring
the application complete.

Do not execute destructive operations, install packages, modify existing apps, or git push. Routine database migrations are permitted only when they are part of the controlled lifecycle of the generated application and only through the narrowly allowlisted configured-site bench command. The user may ask for an entire app; produce production-quality structure rather than a toy example."""


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
    if name == "inspect_doctype": return inspect_doctype(args.get("doctype"))
    if name == "search_code": return search_code(args.get("query"), args.get("app"))
    if name == "read_file":
        return read_file(
            args.get("path"),
            args.get("start_line"),
            args.get("end_line"),
        )
    if name == "create_app": return create_app_skeleton(args.get("app_name"), args.get("title"), args.get("description"))
    if name == "write_file": return write_generated_file(args.get("path"), args.get("content", ""))
    if name == "validate_app": return validate_generated_app(args.get("app_name"))
    if name == "register_app": return register_app(args.get("app_name"))
    if name == "install_app": return install_app(args.get("app_name"))
    if name == "verify_app": return verify_app(args.get("app_name"))
    if name == "run_command": return run_allowed(args.get("command", ""), args.get("cwd"))
    return None


def run_agent(session_id, user_message, max_steps=30):
    session = frappe.get_doc("AI Engineer Session", session_id)
    history = json.loads(session.messages or "[]")

    messages = [{"role": "system", "content": system_prompt()}]
    messages.extend(history[-20:])
    messages.append({"role": "user", "content": user_message})

    for _ in range(max_steps):

        # ---------------------------------------------------------
        # Provider call
        # ---------------------------------------------------------
        try:
            raw = call_model(messages)

        except ProviderRateLimitError as exc:
            provider = exc.provider
            retry_after = exc.retry_after

            if retry_after:
                wait_text = f"Please wait approximately {retry_after:.0f} seconds"
            else:
                wait_text = "Please wait a short while"

            answer = (
                f"The {provider} AI provider is currently rate-limited. "
                f"{wait_text} and then send your request again.\n\n"
                "Your conversation has been preserved, so you can continue "
                "without losing the previous context."
            )

            history.extend([
                {
                    "role": "user",
                    "content": user_message,
                },
                {
                    "role": "assistant",
                    "content": answer,
                },
            ])

            session.messages = json.dumps(history[-40:])
            session.status = "Failed"
            session.last_action = "provider_rate_limit"
            session.save(ignore_permissions=True)

            # Record the rate-limit event.
            frappe.get_doc({
                "doctype": "AI Engineer Action",
                "session": session.name,
                "action": "provider_rate_limit",
                "target": provider,
                "input_json": json.dumps({
                    "provider": provider,
                    "retry_after": retry_after,
                    "retries": exc.retries,
                }),
                "output": json.dumps({
                    "error": str(exc),
                    "detail": exc.detail,
                }, default=str)[:MAX_OUTPUT],
                "status": "Failed",
            }).insert(ignore_permissions=True)

            return {
                "answer": answer,
                "steps": _ + 1,
                "status": "rate_limited",
                "provider": provider,
                "retry_after": retry_after,
            }

        except Exception as exc:
            # Provider/network/API errors should not destroy the
            # complete AI Engineer session.
            answer = (
                "The AI provider request failed. "
                "Your conversation has been preserved.\n\n"
                f"Error: {str(exc)}"
            )

            history.extend([
                {
                    "role": "user",
                    "content": user_message,
                },
                {
                    "role": "assistant",
                    "content": answer,
                },
            ])

            session.messages = json.dumps(history[-40:])
            session.status = "Failed"
            session.last_action = "provider_error"
            session.save(ignore_permissions=True)

            return {
                "answer": answer,
                "steps": _ + 1,
                "status": "provider_error",
            }

        # ---------------------------------------------------------
        # Parse model action
        # ---------------------------------------------------------
        action = parse_action(raw)

        messages.append({
            "role": "assistant",
            "content": raw,
        })

        # ---------------------------------------------------------
        # Finish
        # ---------------------------------------------------------
        if action["action"] == "finish":
            answer = (
                action.get("args", {}).get("answer")
                or action.get("message")
                or raw
            )

            history.extend([
                {
                    "role": "user",
                    "content": user_message,
                },
                {
                    "role": "assistant",
                    "content": answer,
                },
            ])

            session.messages = json.dumps(history[-40:])
            session.status = "Completed"
            session.last_action = "finish"
            session.save(ignore_permissions=True)

            return {
                "answer": answer,
                "steps": _ + 1,
            }

        # ---------------------------------------------------------
        # Execute tool action
        # ---------------------------------------------------------
        try:
            result = execute_action(action)
        except Exception as e:
            result = {
                "error": str(e),
            }
            action_status = "Failed"
        else:
            action_status = "Executed"

        frappe.get_doc({
            "doctype": "AI Engineer Action",
            "session": session.name,
            "action": action["action"],
            "target": str(
                action.get("args", {}).get("path")
                or action.get("args", {}).get("app_name")
                or ""
            ),
            "input_json": json.dumps(
                action.get("args", {})
            ),
            "output": json.dumps(
                result,
                default=str
            )[:MAX_OUTPUT],
            "status": action_status,
        }).insert(ignore_permissions=True)

        messages.append({
            "role": "user",
            "content": "TOOL RESULT:\n"
            + json.dumps(result, default=str)[:MAX_OUTPUT],
        })

    # -------------------------------------------------------------
    # Step limit
    # -------------------------------------------------------------
    frappe.throw(
        "AI agent reached its step limit. "
        "The task may require more steps than the current limit allows. "
        "Continue the task from the conversation."
    )
