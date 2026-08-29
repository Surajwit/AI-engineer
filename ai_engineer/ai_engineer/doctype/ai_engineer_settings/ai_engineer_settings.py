import frappe
from frappe.model.document import Document


class AIEngineerSettings(Document):
    def validate(self):
        if self.ai_provider not in {"Anthropic", "Google", "NVIDIA", "OpenAI"}:
            frappe.throw("AI Provider must be Anthropic, Google, NVIDIA, or OpenAI")

        # Keep the existing NVIDIA base_url setting working for installations
        # upgraded from the original NVIDIA-only version of the app.
        if not self.nvidia_base_url and getattr(self, "legacy_base_url", None):
            self.nvidia_base_url = self.legacy_base_url
        if not self.nvidia_base_url and getattr(self, "base_url", None):
            self.nvidia_base_url = self.base_url

        if not self.model:
            defaults = {
                "Anthropic": "claude-sonnet-5",
                "Google": "gemini-3.7-flash",
                "NVIDIA": "moonshotai/kimi-k2.6",
                "OpenAI": "gpt-5.6-luna",
            }
            self.model = defaults[self.ai_provider]
