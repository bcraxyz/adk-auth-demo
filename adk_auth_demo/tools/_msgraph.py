"""Shared Microsoft Graph implementation.

Both the 2LO and 3LO tools call this with the SAME endpoint. The only
thing that differs is the access token attached, and Graph enforces
server-side which records the caller is allowed to see.

This is the "aha" moment of the demo: identical code, identical query,
divergent results — because the identity behind the request is different.
"""

import httpx

from google.adk.auth.auth_credential import AuthCredential

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def _extract_token(credential: AuthCredential) -> str | None:
    if credential.http and credential.http.credentials:
        return credential.http.credentials.token
    return None


async def graph_list_users(credential: AuthCredential) -> dict:
    """Try to list every user in the tenant.

    The function is identity-blind. It always asks for the same thing.
    What comes back depends on what the token permits.
    """
    token = _extract_token(credential)
    if not token:
        return {"error": "No access token attached to the credential."}

    headers = {"Authorization": f"Bearer {token}"}
    select = "$select=id,displayName,userPrincipalName,jobTitle,department"

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(f"{GRAPH_BASE}/users?{select}", headers=headers)

        if resp.status_code == 200:
            users = resp.json().get("value", [])
            return {
                "endpoint_called": "/v1.0/users",
                "scope_outcome": "tenant-wide visibility granted",
                "user_count": len(users),
                "users": users,
            }

        if resp.status_code in (401, 403):
            # Tenant-wide query rejected. Fall back to /me to surface what
            # the caller IS allowed to see.
            me_resp = await client.get(f"{GRAPH_BASE}/me?{select}", headers=headers)
            if me_resp.status_code == 200:
                return {
                    "endpoint_called": "/v1.0/users (then /v1.0/me)",
                    "scope_outcome": (
                        f"tenant-wide query rejected with {resp.status_code}; "
                        "delegated permission allows access only to the "
                        "signed-in user's own profile"
                    ),
                    "user_count": 1,
                    "users": [me_resp.json()],
                }
            return {
                "endpoint_called": "/v1.0/users (then /v1.0/me)",
                "error": f"both /users ({resp.status_code}) and /me ({me_resp.status_code}) failed",
                "details": me_resp.text,
            }

        return {
            "endpoint_called": "/v1.0/users",
            "error": f"unexpected status {resp.status_code}",
            "details": resp.text,
        }
