frappe.pages["ai-engineer"].on_page_load = function (wrapper) {
    const page = frappe.ui.make_app_page({
        parent: wrapper,
        title: __("AI Engineer"),
        single_column: true,
    });

    const $body = $(page.body);
    $body.html(frappe.render_template("ai_engineer"));
    const chat = $body.find("#ai-chat");
    const input = $body.find("#ai-message");
    const send = $body.find("#ai-send");
    let session = null;

    const addMessage = (role, text) => {
        const cls = role === "user" ? "ai-user" : "ai-assistant";
        $("<div>")
            .addClass(`ai-msg ${cls}`)
            .text(text || "")
            .appendTo(chat);
        chat.scrollTop(chat[0].scrollHeight);
    };

    const ensureSession = () => {
        if (session) return Promise.resolve(session);

        return frappe.call({
            method: "ai_engineer.api.create_session",
            args: { title: __("AI Engineer") },
        }).then((r) => {
            session = r.message;
            return session;
        });
    };

    const sendMessage = () => {
        const text = (input.val() || "").trim();
        if (!text || send.prop("disabled")) return;

        input.val("");
        addMessage("user", text);
        send.prop("disabled", true);

        ensureSession()
            .then((session_name) =>
                frappe.call({
                    method: "ai_engineer.api.chat",
                    args: {
                        session: session_name,
                        message: text,
                    },
                    freeze: true,
                    freeze_message: __("AI Engineer is working..."),
                })
            )
            .then((r) => {
                addMessage(
                    "assistant",
                    (r.message && r.message.answer) || __("No response was returned.")
                );
                send.prop("disabled", false);
            })
            .catch((error) => {
                const message =
                    error?.message ||
                    error?.exc_type ||
                    __("The request failed.");
                addMessage("assistant", message);
                send.prop("disabled", false);
            });
    };

    send.on("click", sendMessage);

    input.on("keydown", (event) => {
        if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
            event.preventDefault();
            sendMessage();
        }
    });

    $body.find("#ai-new-session").on("click", () => {
        session = null;
        chat.empty();
        addMessage("assistant", __("New engineering session started."));
    });

    frappe.call({ method: "ai_engineer.api.get_settings" }).then((r) => {
        const settings = r.message || {};
        const provider = settings.provider || "NVIDIA";
        const model = settings.model || "";
        const configured = settings.has_api_key ? __("configured") : __("API key not configured");
        $body.find("#ai-provider-status").text(
            __("Active provider: {0} · Model: {1} · {2}", [provider, model, configured])
        );
    });

    addMessage(
        "assistant",
        __("I am ready. Describe the Frappe/ERPNext app or feature you want me to engineer.")
    );
};
