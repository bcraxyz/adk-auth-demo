import asyncio
import os
import sys

import google.auth
import httpx
import streamlit as st
import vertexai
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.genai import types as genai_types

load_dotenv()


def _log(msg: str) -> None:
    print(f"[DEMO] {msg}", file=sys.stderr, flush=True)


st.set_page_config(page_title="ADK Auth Demo", page_icon="🔐", layout="wide")

MODES = ["Agent Identity", "OAuth 2LO", "OAuth 3LO", "API Key"]


@st.cache_resource(show_spinner=False)
def get_remote_agent():
    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    location = os.environ["GOOGLE_CLOUD_LOCATION"]
    resource_name = os.environ["AGENT_ENGINE_RESOURCE_NAME"]
    vertexai.init(project=project, location=location)
    client = vertexai.Client(
        project=project,
        location=location,
        http_options=dict(api_version="v1beta1"),
    )
    return client.agent_engines.get(name=resource_name)


@st.cache_resource
def get_global_state() -> dict:
    """Process-wide state keyed by user_id. Survives Streamlit session
    teardown (e.g. OAuth redirect) as long as the same Cloud Run instance
    is hit."""
    return {}


remote_agent = get_remote_agent()


# ─── Session state ──────────────────────────────────────────────────────────
if "user_id" not in st.session_state:
    st.session_state.user_id = "demo-user"
if "messages" not in st.session_state:
    st.session_state.messages = []
if "mode" not in st.session_state:
    st.session_state.mode = (
        get_global_state().get(st.session_state.user_id, {}).get("mode", MODES[0])
    )


def _us() -> dict:
    """Shorthand for the current user's global state dict."""
    return get_global_state().setdefault(st.session_state.user_id, {})


def reset_chat() -> None:
    st.session_state.messages = []
    _us().pop("session_id", None)


# ─── 3LO callback ───────────────────────────────────────────────────────────
def _gcp_token() -> str:
    creds, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    if not creds.valid:
        creds.refresh(Request())
    return creds.token


def maybe_finalize_3lo() -> None:
    """Called on every page load. If Auth Manager redirected back with consent
    state, finalize it and mark the session ready to resume."""
    qp = st.query_params
    state = qp.get("user_id_validation_state")
    provider = qp.get("connector_name")
    if not (state and provider):
        return

    us = _us()
    nonce = us.get("nonce")
    if not nonce:
        st.error("Callback received but no pending authorization found.")
        st.query_params.clear()
        return

    finalize_url = (
        f"https://iamconnectorcredentials.googleapis.com/v1alpha/"
        f"{provider}/credentials:finalize"
    )
    try:
        resp = httpx.post(
            finalize_url,
            json={
                "userId": st.session_state.user_id,
                "userIdValidationState": state,
                "consentNonce": nonce,
            },
            headers={
                "Authorization": f"Bearer {_gcp_token()}",
                "Content-Type": "application/json",
            },
            timeout=15.0,
        )
        _log(f"finalize status={resp.status_code} body={resp.text[:500]}")
        resp.raise_for_status()
    except httpx.HTTPError as e:
        st.error(f"Failed to finalize consent: {e}")
        return

    # auth_config and fc_id were saved during the original turn and are
    # still in global state — the resume turn will use them.
    us["resume_pending"] = True
    us.pop("auth_uri", None)
    us.pop("nonce", None)
    st.query_params.clear()
    st.success("✓ Consent granted. Resuming...")


maybe_finalize_3lo()


# ─── Sidebar ────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("🔐 ADK Auth Demo")
    new_mode = st.radio(
        "Pick an authentication mode:",
        options=MODES,
        index=MODES.index(st.session_state.mode),
        key="mode_radio",
    )
    if new_mode != st.session_state.mode:
        st.session_state.mode = new_mode
        _us()["mode"] = new_mode
        reset_chat()
        st.rerun()
    else:
        _us()["mode"] = new_mode

    st.markdown("---")
    st.caption("**Agent Identity** — Lists GCS buckets using the agent's SPIFFE-bound identity.")
    st.caption("**OAuth 2LO** — Lists users in Entra using the agent's app-only token.")
    st.caption("**OAuth 3LO** — Same query as 2LO, but as the signed-in user. Graph constrains the result.")
    st.caption("**API Key** — Sends an email via Resend. Key fetched from Auth Manager at call time.")

    if _us().get("auth_uri"):
        st.markdown("---")
        st.caption("Click to authorize. After redirect, your request will resume automatically.")
        st.markdown(
            f'<a href="{_us()["auth_uri"]}" target="_self" '
            f'style="display:inline-block;padding:0.5rem 1rem;background:#2e66f5;'
            f'color:white;text-decoration:none;border-radius:0.5rem;'
            f'text-align:center;width:100%;">→ Authorize</a>',
            unsafe_allow_html=True,
        )


# ─── Chat history ────────────────────────────────────────────────────────────
for role, text in st.session_state.messages:
    with st.chat_message(role):
        st.markdown(text)


# ─── Helpers ─────────────────────────────────────────────────────────────────
def _find_auth_request(event_dict: dict) -> dict | None:
    content = event_dict.get("content") or {}
    for part in content.get("parts") or []:
        fc = part.get("function_call") or part.get("functionCall")
        if fc and fc.get("name") == "adk_request_credential":
            return fc
    return None


def _extract_consent(fc: dict) -> tuple[str | None, str | None, dict | None]:
    args = fc.get("args") or {}
    auth_config = args.get("auth_config") or args.get("authConfig") or {}
    exchanged = (
        auth_config.get("exchanged_auth_credential")
        or auth_config.get("exchangedAuthCredential")
        or {}
    )
    oauth2 = exchanged.get("oauth2") or {}
    auth_uri = oauth2.get("auth_uri") or oauth2.get("authUri")
    nonce = oauth2.get("nonce")
    return auth_uri, nonce, auth_config


async def ensure_session() -> str:
    us = _us()
    if us.get("session_id"):
        return us["session_id"]
    session = await remote_agent.async_create_session(user_id=st.session_state.user_id)
    sid = session["id"] if isinstance(session, dict) else session.id
    us["session_id"] = sid
    return sid


async def run_turn(user_prompt: str) -> str:
    session_id = await ensure_session()
    us = _us()

    if us.get("resume_pending") and us.get("auth_config") and us.get("fc_id"):
        message = genai_types.Content(
            role="user",
            parts=[
                genai_types.Part(
                    function_response=genai_types.FunctionResponse(
                        id=us["fc_id"],
                        name="adk_request_credential",
                        response=us["auth_config"],
                    )
                )
            ],
        )
        us.pop("resume_pending", None)
        us.pop("auth_config", None)
        us.pop("fc_id", None)
        _log("run_turn: resuming with function_response")
    else:
        message = f"[Mode: {st.session_state.mode}] {user_prompt}"
        _log(f"run_turn: fresh prompt, mode={st.session_state.mode}")

    final_text: list[str] = []
    async for event in remote_agent.async_stream_query(
        user_id=st.session_state.user_id,
        session_id=session_id,
        message=message,
    ):
        ev = event if isinstance(event, dict) else event.model_dump()

        fc = _find_auth_request(ev)
        if fc:
            auth_uri, nonce, auth_config = _extract_consent(fc)
            if auth_uri and nonce:
                us["auth_uri"] = auth_uri
                us["nonce"] = nonce
                us["auth_config"] = auth_config
                us["fc_id"] = fc.get("id")
                return "I need your consent to act on your behalf. Click **Authorize** in the sidebar."

        content = ev.get("content") or {}
        if content.get("role") == "model":
            for part in content.get("parts") or []:
                if part.get("text"):
                    final_text.append(part["text"])

    return "".join(final_text) or "(no response)"


# ─── Chat input ──────────────────────────────────────────────────────────────
# Auto-resume: if consent was just granted, immediately send the function_response
# without waiting for user input.
if _us().get("resume_pending"):
    with st.chat_message("assistant"):
        with st.spinner("Resuming…"):
            reply = asyncio.run(run_turn(""))
        st.markdown(reply)
    st.session_state.messages.append(("assistant", reply))
    st.rerun()

prompt = st.chat_input("Try: 'list my storage buckets' / 'send a test email' / 'list users in Entra'")
if prompt:
    st.session_state.messages.append(("user", prompt))
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        with st.spinner("Thinking…"):
            reply = asyncio.run(run_turn(prompt))
        st.markdown(reply)
    st.session_state.messages.append(("assistant", reply))
    if _us().get("auth_uri"):
        st.rerun()
