import json
import time

import frappe
import requests


DEFAULT_TIMEOUT = 280
MAX_ERROR = 2000

# Provider rate-limit resilience.
#
# We retry once after a 429 because providers such as Gemini may return
# a very specific Retry-After / retryDelay value. Retrying multiple times
# can make one AI Engineer request block for several minutes.
MAX_RATE_LIMIT_RETRIES = 1
DEFAULT_RATE_LIMIT_WAIT = 5
MAX_RATE_LIMIT_WAIT = 120


class ProviderRateLimitError(Exception):
    """
    Raised when an AI provider remains rate-limited after the configured
    retry attempts.

    This is intentionally NOT a frappe.throw() exception. The agent layer
    can catch it and return a clean response to the UI instead of turning
    the request into HTTP 417.
    """

    def __init__(
        self,
        provider,
        status_code=429,
        retry_after=None,
        detail=None,
        retries=0,
    ):
        self.provider = provider
        self.status_code = status_code
        self.retry_after = retry_after
        self.detail = detail or ""
        self.retries = retries

        message = (
            f"{provider} API rate limit exceeded. "
            "Please wait before sending another request."
        )

        if retry_after is not None:
            message += f" Suggested retry delay: {retry_after:.0f}s."

        super().__init__(message)


def get_settings():
    return frappe.get_single("AI Engineer Settings")


def api_key(settings, fieldname, label):
    if not getattr(settings, fieldname, None):
        frappe.throw(f"Configure the {label} in AI Engineer Settings")
    return settings.get_password(fieldname)


def _response_detail(response):
    """Return a compact provider error payload."""
    try:
        detail = response.json()
        return json.dumps(detail, ensure_ascii=False)
    except ValueError:
        return response.text or ""


def _retry_delay(response, attempt):
    """
    Determine how long to wait before retrying a rate-limited request.

    Priority:
    1. HTTP Retry-After header
    2. Provider-specific retryDelay, e.g. Gemini:
       {"details": [{"@type": "...RetryInfo", "retryDelay": "54s"}]}
    3. Exponential backoff
    """

    retry_after = response.headers.get("Retry-After")

    if retry_after:
        try:
            return min(max(float(retry_after), 0), MAX_RATE_LIMIT_WAIT)
        except (ValueError, TypeError):
            pass

    try:
        data = response.json()

        for detail in data.get("error", {}).get("details", []):
            retry_delay = detail.get("retryDelay")

            if not retry_delay:
                continue

            if isinstance(retry_delay, str):
                value = retry_delay.strip().lower()

                if value.endswith("s"):
                    value = value[:-1]

                try:
                    return min(
                        max(float(value), 0),
                        MAX_RATE_LIMIT_WAIT,
                    )
                except ValueError:
                    pass

    except (ValueError, TypeError, AttributeError):
        pass

    # 5s, 10s, 20s ... capped at MAX_RATE_LIMIT_WAIT.
    return min(
        DEFAULT_RATE_LIMIT_WAIT * (2 ** attempt),
        MAX_RATE_LIMIT_WAIT,
    )


def post_json(
    url,
    headers,
    payload,
    provider,
    timeout=DEFAULT_TIMEOUT,
    max_rate_limit_retries=MAX_RATE_LIMIT_RETRIES,
):
    """
    POST JSON to an AI provider with centralized rate-limit resilience.

    HTTP 429 responses are retried using the provider's requested delay
    where available.

    If the provider remains rate-limited after the retry attempts,
    ProviderRateLimitError is raised instead of frappe.throw(), allowing
    the AI agent layer to return a clean response to the UI.
    """

    last_response = None
    last_retry_delay = None

    for attempt in range(max_rate_limit_retries + 1):

        try:
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=timeout,
            )

        except requests.exceptions.ReadTimeout:
            frappe.throw(
                f"{provider} API request timed out after {timeout}s. "
                "Try a smaller prompt or max tokens value."
            )

        except requests.exceptions.RequestException as exc:
            frappe.throw(
                f"{provider} API connection error: {exc}"
            )

        last_response = response

        if response.ok:
            break

        # ---------------------------------------------------------
        # Rate limit handling
        # ---------------------------------------------------------
        if response.status_code == 429:

            last_retry_delay = _retry_delay(
                response,
                attempt,
            )

            if attempt < max_rate_limit_retries:

                frappe.logger("ai_engineer").warning(
                    f"{provider} rate limit reached (HTTP 429). "
                    f"Retry {attempt + 1}/{max_rate_limit_retries} "
                    f"in {last_retry_delay:.1f}s."
                )

                time.sleep(last_retry_delay)
                continue

            # No retries remaining.
            detail = _response_detail(response)

            raise ProviderRateLimitError(
                provider=provider,
                status_code=response.status_code,
                retry_after=last_retry_delay,
                detail=detail,
                retries=attempt,
            )

        # Any non-429 error exits immediately.
        break

    response = last_response

    if not response.ok:

        detail = _response_detail(response)

        frappe.throw(
            f"{provider} API error {response.status_code}: "
            f"{detail[:MAX_ERROR]}"
        )

    try:
        return response.json()

    except ValueError:
        frappe.throw(
            f"{provider} API returned invalid JSON"
        )
