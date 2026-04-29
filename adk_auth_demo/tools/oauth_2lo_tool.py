"""Tool 3: OAuth 2LO via Auth Manager → Microsoft Graph.

The agent acts on its OWN authority. Auth Manager holds the Microsoft
Entra client_id/client_secret; the ADK fetches a fresh app-only access
token at tool-call time and injects it into the function call.

App permission User.Read.All has been granted and admin-consented on the
Microsoft Entra app registration, so the token can list the whole tenant.
"""

import os

from google.adk.auth.auth_credential import AuthCredential
from google.adk.auth.auth_tool import AuthConfig
from google.adk.auth.credential_manager import CredentialManager
from google.adk.integrations.agent_identity import (
    GcpAuthProvider,
    GcpAuthProviderScheme,
)
from google.adk.tools.authenticated_function_tool import AuthenticatedFunctionTool

from adk_auth_demo.tools._msgraph import graph_list_users

CredentialManager.register_auth_provider(GcpAuthProvider())


async def list_microsoft_users_app(credential: AuthCredential) -> dict:
    """List users in the Microsoft 365 tenant using the agent's app-only token."""
    return await graph_list_users(credential)


def build() -> AuthenticatedFunctionTool:
    auth_config = AuthConfig(
        auth_scheme=GcpAuthProviderScheme(
            name=os.environ["AUTH_PROVIDER_MSGRAPH_2LO"]
        )
    )
    return AuthenticatedFunctionTool(
        func=list_microsoft_users_app,
        auth_config=auth_config,
    )
