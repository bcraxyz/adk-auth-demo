"""Tool 4: OAuth 3LO via Auth Manager → Microsoft Graph."""

import os
from google.adk.auth.auth_credential import AuthCredential
from google.adk.auth.auth_tool import AuthConfig
from google.adk.integrations.agent_identity import GcpAuthProviderScheme
from google.adk.tools.authenticated_function_tool import AuthenticatedFunctionTool
from adk_auth_demo.tools._msgraph import graph_list_users

async def list_microsoft_users_delegated(credential: AuthCredential) -> dict:
    """List users in the Microsoft 365 tenant using the signed-in user's token.
    Same query as 2LO; Microsoft Graph constrains the result based on the
    delegated scope the user consented to."""
    return await graph_list_users(credential)

def build() -> AuthenticatedFunctionTool:
    return AuthenticatedFunctionTool(
        func=list_microsoft_users_delegated,
        auth_config=AuthConfig(
            auth_scheme=GcpAuthProviderScheme(
                name=os.environ["AUTH_PROVIDER_MSGRAPH_3LO"],
                continue_uri=os.environ["REDIRECT_URI"],
                scopes=["User.Read", "offline_access"],
            )
        ),
    )
