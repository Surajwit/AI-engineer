import frappe

def run():
    s = frappe.get_doc("AI Engineer Settings")
    print("base_url:", repr(s.base_url))
    print("model:", repr(s.model))
    print("has key:", bool(s.nvidia_api_key))
