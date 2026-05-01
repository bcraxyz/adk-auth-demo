"""Tool 2: API key via Auth Manager → Resend."""

import os
import google.auth
import requests
import resend
from google.adk.tools import FunctionTool
from google.auth.transport.requests import Request

_AUTH_MANAGER_BASE = "https://iamconnectorcredentials.googleapis.com/v1alpha"

def _retrieve_api_key(auth_provider_name: str) -> str:
    """Fetch a stored API key from Auth Manager. The deployed agent's SPIFFE
    identity authenticates via ADC; Auth Manager checks roles/iamconnectors.user."""
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
    if resp.status_code != 200:
        raise RuntimeError(
            f"Auth Manager API failed with status {resp.status_code}. "
            f"URL: {url} | Details: {resp.text}"
        )
    api_key = resp.json()["response"]["token"]
    if not api_key:
        raise RuntimeError(f"No API key returned for {auth_provider_name}.")
    return api_key

def send_email(to_email: str, subject: str, body: str) -> dict:
    """Send an email via Resend.
    Args:
        to_email: Recipient's email address.
        subject: Email subject line.
        body: Plain text or HTML body.
    Returns:
        Dict with the recipient, subject, and the auth provider used.
    """
    provider = os.environ["AUTH_PROVIDER_RESEND"]
    from_email = os.environ["RESEND_FROM_EMAIL"]

    resend.api_key = _retrieve_api_key(provider)
    resend.Emails.send(
        {
            "from": from_email,
            "to": [to_email],
            "subject": subject,
            "html": body if body.lstrip().startswith("<") else f"<p>{body}</p>",
        }
    )
    return {
        "to": to_email,
        "subject": subject,
        "auth_provider": provider.split("/")[-1],
    }

def build() -> FunctionTool:
    return FunctionTool(func=send_email)
