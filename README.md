# AI Engineer for Frappe / ERPNext

An ERPNext/Frappe app that provides an AI engineering workspace powered by NVIDIA NIM-compatible APIs.

## What it does
- Chat with an AI engineer from the ERPNext Desk.
- Inspect installed apps and source code through controlled tools.
- Search the codebase with ripgrep when available.
- Generate complete Frappe apps into `apps/<app_name>`.
- Review generated files before installation.
- Run safe validation commands on generated apps.
- Install generated apps only after explicit approval.
- Keep an audit trail of agent actions.

## Install
```bash
bench get-app ai_engineer /path/to/ai_engineer
bench --site yoursite install-app ai_engineer
bench --site yoursite migrate
```

Then open **AI Engineer** from the Desk.

## NVIDIA
Configure an API key in **AI Engineer Settings**. The default endpoint is the NVIDIA NIM chat endpoint. NIM hosted endpoints are intended for prototyping/development under NVIDIA's Developer Program; production licensing/terms should be checked before deployment.


## Frappe/ERPNext v16

This release targets Frappe Framework 16 and ERPNext 16.

### Install on an existing site

From the bench directory:

```bash
bench --site site1.local install-app ai_engineer
bench build
bench --site site1.local migrate
bench --site site1.local clear-cache
```

Then restart the development server with `bench start` and open:

`http://site1.local:8000/app/ai-engineer`

The page requires the **System Manager** role.

### NVIDIA NIM

The default model is `moonshotai/kimi-k2.6` and the default endpoint is NVIDIA's OpenAI-compatible `/v1/chat/completions` endpoint. Configure the API key in **AI Engineer Settings**.

Keep **Allow Code Execution** disabled until you have reviewed the generated code and explicitly decided which commands to allow.

## Multi-provider AI configuration

AI Engineer supports switching the active model provider from **AI Engineer Settings** without changing the engineering agent, tools, workspace, or session logic.

Supported providers:

- **Anthropic** — Claude API
- **Google** — Gemini API / Google AI Studio
- **NVIDIA** — NVIDIA NIM hosted API
- **OpenAI** — OpenAI API

### Configure a provider

1. Open **AI Engineer Settings** as a System Manager.
2. Select **AI Provider**.
3. Enter the provider's API key.
4. Enter the model ID you want to use, or keep the provider default.
5. Save.
6. Use **Test Active Provider** to verify the credentials and model.

The AI Engineer page also displays the active provider and model.

### Provider model examples

| Provider | Example model |
|---|---|
| Anthropic | `claude-sonnet-5` |
| Google | `gemini-3.7-flash` |
| NVIDIA | `moonshotai/kimi-k2.6` |
| OpenAI | `gpt-5.6-luna` |

Model IDs are configurable rather than hard-coded, so the provider adapter can use other compatible models supported by the selected API.

### Backward compatibility

The original NVIDIA settings are preserved. Existing `nvidia_api_key`, `base_url`, and `model` values remain usable, and NVIDIA remains the default provider for an existing installation.
