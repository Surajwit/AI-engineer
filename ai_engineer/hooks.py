app_name = "ai_engineer"
app_title = "AI Engineer"
app_publisher = "AI Engineer"
app_description = "AI engineering agent for Frappe and ERPNext"
app_email = "dev@example.com"
app_license = "MIT"

required_apps = ["frappe", "erpnext"]

app_include_css = "ai_engineer/css/ai_engineer.css"

scheduler_events = {
    "hourly": [
        "ai_engineer.api.cleanup_old_sessions",
    ]
}

add_to_apps_screen = [
    {
        "name": "ai_engineer",
        "title": "AI Engineer",
        "route": "/app/ai-engineer",
        "logo": "/assets/ai_engineer/images/ai-engineer.svg",
        "has_permission": "ai_engineer.api.has_app_permission",
    }
]
