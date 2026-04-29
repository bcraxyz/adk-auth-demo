"""Tool 2: API key via Auth Manager → Resend.

The agent never has the raw API key in its environment. It calls the
Auth Manager retrieveCredentials endpoint at tool-invocation time using
its SPIFFE identity, holds the key in memory only for the duration of
the call, and discards it.

This makes Auth Manager the credential vault for the API key, parallel
to how it acts as the vault for the OAuth tokens in tools 3 and 4.
"""

import os

import google.auth
import requests
import resend
from google.adk.tools import FunctionTool
from google.auth.transport.requests import Request

_AUTH_MANAGER_BASE = "https://iamconnectorcredentials.googleapis.com/v1alpha"


def _retrieve_api_key(auth_provider_name: str) -> str:
    """Fetch a stored API key from Auth Manager.

    The deployed agent's SPIFFE identity authenticates to Auth Manager
    via ADC. Auth Manager checks roles/iamconnectors.user on the auth
    provider resource and returns the secret if authorized.
    """
    creds, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    if not creds.valid:
        creds.refresh(Request())

    url = f"{_AUTH_MANAGER_BASE}/{auth_provider_name}/credentials:retrieve"
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {creds.token}",
            "Content-Type": "application/json",
        },
        json={},
        timeout=10,
    )
    resp.raise_for_status()
    payload = resp.json()
    api_key = payload.get("apiKey", {}).get("apiKey")
    if not api_key:
        raise RuntimeError(
            f"Auth Manager returned no apiKey field for {auth_provider_name}. "
            f"Response keys: {list(payload.keys())}"
        )
    return api_key


def send_demo_email(subject: str, body: str) -> dict:
    """Send a short demo email via Resend.

    Args:
        subject: Email subject line.
        body: Plain text or HTML body.

    Returns:
        Dict with the Resend message ID and recipient.
    """
    provider = os.environ["AUTH_PROVIDER_RESEND"]
    from_addr = os.environ["RESEND_FROM_EMAIL"]
    to_addr = os.environ["RESEND_TO_EMAIL"]

    resend.api_key = _retrieve_api_key(provider)
    result = resend.Emails.send(
        {
            "from": from_addr,
            "to": [to_addr],
            "subject": subject,
            "html": body if body.lstrip().startswith("<") else f"<p>{body}</p>",
        }
    )
    return {
        "message_id": result.get("id"),
        "to": to_addr,
        "subject": subject,
        "credential_source": "Auth Manager (API_KEY provider)",
    }


def build() -> FunctionTool:
    return FunctionTool(func=send_demo_email)
