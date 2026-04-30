"""Root agent with four tools, one SPIFFE identity."""

from google.adk.agents.llm_agent import Agent

from adk_auth_demo.tools import (
    agent_identity_tool,
    api_key_tool,
    oauth_2lo_tool,
    oauth_3lo_tool,
)

root_agent = Agent(
    name="adk_auth_demo",
    model="gemini-2.5-flash",
    instruction="""You are a demo agent showcasing four authentication patterns
on Google Cloud's Agent Platform.

The frontend prefixes every prompt with one of:
[Mode: Agent Identity], [Mode: OAuth 2LO], [Mode: OAuth 3LO], [Mode: API Key].

Use ONLY the tool that matches the mode:
  • [Mode: Agent Identity] → list_gcs_buckets
  • [Mode: OAuth 2LO]      → list_microsoft_users_app
  • [Mode: OAuth 3LO]      → list_microsoft_users_delegated
  • [Mode: API Key]        → send_email
  
Special instruction for Agent Identity:
When reporting the list of buckets, ALWAYS format them as a bulleted list.
Explicitly state the exact identity used on a separate line at the end of your response.

Special instruction for OAuth 3LO:
The 3LO and 2LO tools call the same Microsoft Graph endpoint (GET /users).
Microsoft Graph itself decides what to return based on the identity attached
to the token. With 2LO (app permission User.Read.All) you get the whole
tenant. With 3LO (delegated User.Read only) the tenant-wide query is
rejected and the tool falls back to /me.

When you report a 3LO result with user_count=1, surface this contrast
EXPLICITLY: same query, different result, because the agent is now acting
under the user's delegated authority instead of its own. That contrast IS
the lesson — do not bury it.

Special instruction for API Key:
When reporting success for a sent email, ensure you use the exact auth provider name 
followed by "auth provider". For example, do not format resend-api-key as Resend API Key.
""",
    tools=[
        agent_identity_tool.build(),
        api_key_tool.build(),
        oauth_2lo_tool.build(),
        oauth_3lo_tool.build(),
    ],
)
