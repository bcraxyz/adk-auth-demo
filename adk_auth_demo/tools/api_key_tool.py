"""Tool 2: API key via Auth Manager → Resend."""

import os
import requests
import resend
import google.auth
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
        json={"user_id": "agent-runtime-context"},
        timeout=10,
    )
    
    if resp.status_code != 200:
        raise RuntimeError(
            f"Auth Manager API failed with status {resp.status_code}."
            f"URL: {url} | Details: {resp.text}"
        )
    
    payload = resp.json()
    api_key = payload["response"]["token"]
    
    if not api_key:
        raise RuntimeError(
            f"Auth Manager returned no API key field for {auth_provider_name}. "
        )
    return api_key

def send_email(to_email: str, subject: str, body: str) -> dict:
    """Send an email via Resend.
    Args:
        to_email: Recipient's email address.
        subject: Email subject line.
        body: Plain text or HTML body.

    Returns:
        Dict with the Resend message ID and recipient.
    """
    provider = os.environ["AUTH_PROVIDER_RESEND"]
    from_email = os.environ["RESEND_FROM_EMAIL"]

    resend.api_key = _retrieve_api_key(provider)
    result = resend.Emails.send(
        {
            "from": from_email,
            "to": [to_email],
            "subject": subject,
            "html": body if body.lstrip().startswith("<") else f"<p>{body}</p>",
        }
    )
    return {
        "message_id": result.get("id"),
        "to": to_addr,
        "subject": subject,
        "credential_source": provider.split("/")[-1],
    }

def build() -> FunctionTool:
    return FunctionTool(func=send_email)
