frappe.ui.form.on("AI Engineer Settings", {
    refresh(frm) {
        frm.set_intro(
            __("Select a provider and model below. The agent, tools, ERPNext context, and safety controls remain unchanged when you switch providers.")
        );

        frm.add_custom_button(__("Test Active Provider"), () => {
            frappe.call({
                method: "ai_engineer.api.test_provider",
                freeze: true,
                freeze_message: __("Testing AI provider connection..."),
            }).then((r) => {
                const result = r.message || {};
                frappe.msgprint({
                    title: __("AI Provider Test"),
                    message: result.message || __("Provider test completed."),
                    indicator: result.ok ? "green" : "red",
                });
            });
        });
    },

    ai_provider(frm) {
        const defaults = {
            Anthropic: "claude-sonnet-5",
            Google: "gemini-3.7-flash",
            NVIDIA: "moonshotai/kimi-k2.6",
            OpenAI: "gpt-5.6-luna",
        };
        frm.set_value("model", defaults[frm.doc.ai_provider]);
    },
});
