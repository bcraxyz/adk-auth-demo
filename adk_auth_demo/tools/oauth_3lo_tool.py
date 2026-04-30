"""Tool 4: OAuth 3LO via Auth Manager → Microsoft Graph."""

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

async def list_microsoft_users_delegated(credential: AuthCredential) -> dict:
    """List users in the Microsoft 365 tenant using the signed-in user's token.
    The query is identical to the 2LO tool's. Microsoft Graph constrains
    the result based on the delegated scope the user consented to.
    """
    return await graph_list_users(credential)

def build() -> AuthenticatedFunctionTool:
    auth_config = AuthConfig(
        auth_scheme=GcpAuthProviderScheme(
            name=os.environ["AUTH_PROVIDER_MSGRAPH_3LO"],
            continue_uri=os.environ["REDIRECT_URI"],
            scopes=["User.Read", "offline_access"]
        )
    )
    return AuthenticatedFunctionTool(
        func=list_microsoft_users_delegated,
        auth_config=auth_config,
    )
