"""
Graphene Slack Bot
------------------
- Channel: @mention to start a thread. Follow-up replies in that thread
  need no mention — the bot stays in the conversation automatically.
- DM: message the bot directly. Access controlled by Graphene's
  Users & Access admin page (Slack User ID → allowed agents mapping).
- Both modes maintain full conversation history so Claude has context.

Run:
  cd slack-bot && source venv/bin/activate && python bot.py
"""

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Optional

import anthropic
import httpx
from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "frontend" / "data"

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def load_providers():
    return json.loads((DATA_DIR / "providers.json").read_text())

def load_bindings():
    return json.loads((DATA_DIR / "channel-bindings.json").read_text())

def load_llm_providers():
    return json.loads((DATA_DIR / "llm-providers.json").read_text())

def load_users():
    try:
        return json.loads((DATA_DIR / "users.json").read_text())
    except Exception:
        return []

def find_binding(channel_id: str) -> Optional[dict]:
    return next((b for b in load_bindings() if b["channelId"] == channel_id), None)

def find_user(slack_user_id: str) -> Optional[dict]:
    """Look up a Slack user in Graphene's Users & Access mapping."""
    return next(
        (u for u in load_users() if u.get("slackUserId") == slack_user_id and u.get("status") == "active"),
        None,
    )

def get_active_llm() -> Optional[dict]:
    return next((p for p in load_llm_providers() if p.get("active") and p.get("apiKey")), None)

# ---------------------------------------------------------------------------
# Agent system prompts
# ---------------------------------------------------------------------------

SYSTEM_PROMPTS = {
    "Portfolio Agent":  "You are a portfolio analyst assistant. Use the available tools to answer questions about positions, P&L, history, and transactions. Be concise and professional. Format numbers clearly.",
    "Advisor Agent":    "You are an investment advisor. Use portfolio and market data tools to give tailored recommendations.",
    "Report Agent":     "You are a report generation assistant. Help create portfolio reports and summaries from available data.",
    "Compliance Agent": "You are a compliance officer assistant. Surface audit events and flag anomalies.",
}

MCP_URL = "http://localhost:8001/mcp"

# ---------------------------------------------------------------------------
# Conversation history helpers
# ---------------------------------------------------------------------------

def _strip_mention(text: str) -> str:
    return re.sub(r"<@[A-Z0-9]+>", "", text).strip()

async def build_thread_history(client, channel_id: str, thread_ts: str) -> list:
    """Fetch all messages in a thread and return as Claude conversation history."""
    try:
        result = await client.conversations_replies(channel=channel_id, ts=thread_ts, limit=30)
        messages = []
        for msg in result.get("messages", []):
            text = _strip_mention(msg.get("text", ""))
            if not text:
                continue
            role = "assistant" if msg.get("bot_id") else "user"
            messages.append({"role": role, "content": text})
        return messages
    except Exception as e:
        log.warning(f"Could not fetch thread history: {e}")
        return []

async def build_dm_history(client, channel_id: str, current_ts: str) -> list:
    """Fetch DM history (excluding current message) as Claude conversation history."""
    try:
        result = await client.conversations_history(channel=channel_id, limit=30)
        messages = []
        for msg in reversed(result.get("messages", [])):
            if msg.get("ts") == current_ts:
                continue
            text = msg.get("text", "").strip()
            if not text:
                continue
            role = "assistant" if msg.get("bot_id") else "user"
            messages.append({"role": role, "content": text})
        return messages
    except Exception as e:
        log.warning(f"Could not fetch DM history: {e}")
        return []

# ---------------------------------------------------------------------------
# Agentic loop: MCP tools + Claude
# ---------------------------------------------------------------------------

async def run_agent(messages: list, agent_name: str, mcp_url: str) -> str:
    """
    Discover tools via GET {base_url}/tools, then run the Claude loop.
    Tool calls are executed via POST {base_url}/call (plain httpx, no anyio).
    """
    base_url = mcp_url.rstrip("/")
    if base_url.endswith("/mcp"):
        base_url = base_url[:-4]

    anthropic_tools = []
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{base_url}/tools")
            resp.raise_for_status()
            tools_data = resp.json().get("tools", [])
        anthropic_tools = [
            {
                "name": t["name"],
                "description": t.get("description", ""),
                "input_schema": t.get("inputSchema", {"type": "object", "properties": {}}),
            }
            for t in tools_data
        ]
        log.info(f"Discovered {len(anthropic_tools)} tools from {base_url}")
    except Exception as e:
        log.warning(f"MCP server unavailable at {base_url}: {e}. Running without tools.")

    return await _llm_loop(messages, agent_name, anthropic_tools, base_url)


async def _llm_loop(
    messages: list,
    agent_name: str,
    anthropic_tools: list,
    base_url: str,
) -> str:
    llm_config = get_active_llm()
    if not llm_config:
        return "No LLM provider is configured. Ask an admin to add an API key in Graphene → LLM Providers."

    llm = anthropic.Anthropic(api_key=llm_config["apiKey"])
    model = llm_config.get("activeModel", "claude-sonnet-4-6")
    system = SYSTEM_PROMPTS.get(agent_name, "You are a helpful assistant.")

    # Work on a copy so we can append tool results without mutating the caller's list
    convo = list(messages)

    for iteration in range(6):
        kwargs: dict = {
            "model": model,
            "max_tokens": 2048,
            "system": system,
            "messages": convo,
        }
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools

        response = llm.messages.create(**kwargs)
        log.info(f"Claude stop_reason={response.stop_reason} iteration={iteration}")

        if response.stop_reason != "tool_use":
            return next(
                (b.text for b in response.content if b.type == "text"),
                "I was unable to generate a response.",
            )

        convo.append({"role": "assistant", "content": response.content})

        tool_results = []
        async with httpx.AsyncClient(timeout=10.0) as client:
            for block in response.content:
                if block.type != "tool_use":
                    continue
                log.info(f"Tool call: {block.name}({block.input})")
                try:
                    resp = await client.post(
                        f"{base_url}/call",
                        json={"name": block.name, "arguments": block.input or {}},
                    )
                    resp.raise_for_status()
                    content_text = json.dumps(resp.json().get("result", {}))
                except Exception as e:
                    content_text = f"Tool error: {e}"

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": content_text,
                })

        convo.append({"role": "user", "content": tool_results})

    return "I was unable to complete the request within the allowed steps."


# ---------------------------------------------------------------------------
# Slack event handlers
# ---------------------------------------------------------------------------

def build_app(provider: dict) -> AsyncApp:
    app = AsyncApp(
        token=provider["botToken"],
        signing_secret=provider["signingSecret"],
    )

    @app.event("app_mention")
    async def handle_mention(event, say, client):
        channel_id = event["channel"]
        thread_ts = event.get("thread_ts") or event["ts"]

        binding = find_binding(channel_id)
        if not binding:
            await say(
                text="This channel has no agent binding. Ask an admin to configure one in Graphene → Channel Binding.",
                thread_ts=thread_ts,
            )
            return

        agent_name = binding.get("agent", "Portfolio Agent")
        user_text = _strip_mention(event.get("text", ""))
        if not user_text:
            return

        log.info(f"[{agent_name}] mention channel={channel_id} user={event.get('user')} msg={user_text!r}")

        try:
            await client.reactions_add(channel=channel_id, name="thinking_face", timestamp=event["ts"])
        except Exception:
            pass

        try:
            # Fetch thread history for context (excludes current message — it's not posted yet)
            history = await build_thread_history(client, channel_id, thread_ts)
            # Remove the last entry if it matches the current message (already in history)
            messages = history if (history and history[-1]["content"] == user_text) else [*history, {"role": "user", "content": user_text}]
            response_text = await run_agent(messages, agent_name, MCP_URL)
        except Exception as e:
            log.exception(f"Agent error: {e}")
            response_text = f"Sorry, something went wrong: {e}"

        try:
            await client.reactions_remove(channel=channel_id, name="thinking_face", timestamp=event["ts"])
        except Exception:
            pass

        await say(text=response_text, thread_ts=thread_ts)

    @app.event("message")
    async def handle_message(event, say, client):
        # Ignore bot messages and edited/deleted subtypes
        if event.get("bot_id") or event.get("subtype"):
            return

        channel_type = event.get("channel_type")

        # ── DM handler ──────────────────────────────────────────────────────
        if channel_type == "im":
            slack_user_id = event.get("user", "")
            user_text = event.get("text", "").strip()
            if not user_text:
                return

            user_record = find_user(slack_user_id)
            if not user_record:
                await say(text="You don't have access. Ask your admin to add you in Graphene → Users & Access.")
                return

            allowed_agents = user_record.get("agents", [])
            if not allowed_agents:
                await say(text="Your account has no agents assigned. Contact your admin.")
                return

            agent_name = allowed_agents[0]
            log.info(f"[DM] user={slack_user_id} ({user_record['name']}) agent={agent_name} msg={user_text!r}")

            try:
                history = await build_dm_history(client, event["channel"], event["ts"])
                messages = [*history, {"role": "user", "content": user_text}]
                response_text = await run_agent(messages, agent_name, MCP_URL)
            except Exception as e:
                log.exception(f"DM agent error: {e}")
                response_text = f"Sorry, something went wrong: {e}"

            await say(text=response_text)
            return

        # ── Thread follow-up handler ─────────────────────────────────────────
        thread_ts = event.get("thread_ts")
        if not thread_ts:
            return  # not in a thread — require @mention to start one

        channel_id = event["channel"]
        binding = find_binding(channel_id)
        if not binding:
            return

        # Only respond if the bot already participated in this thread
        try:
            replies = await client.conversations_replies(channel=channel_id, ts=thread_ts, limit=50)
            bot_in_thread = any(m.get("bot_id") for m in replies.get("messages", []))
        except Exception:
            bot_in_thread = False

        if not bot_in_thread:
            return

        agent_name = binding.get("agent", "Portfolio Agent")
        user_text = _strip_mention(event.get("text", ""))
        if not user_text:
            return

        log.info(f"[{agent_name}] thread reply channel={channel_id} user={event.get('user')} msg={user_text!r}")

        try:
            await client.reactions_add(channel=channel_id, name="thinking_face", timestamp=event["ts"])
        except Exception:
            pass

        try:
            history = await build_thread_history(client, channel_id, thread_ts)
            messages = history if (history and history[-1]["content"] == user_text) else [*history, {"role": "user", "content": user_text}]
            response_text = await run_agent(messages, agent_name, MCP_URL)
        except Exception as e:
            log.exception(f"Thread agent error: {e}")
            response_text = f"Sorry, something went wrong: {e}"

        try:
            await client.reactions_remove(channel=channel_id, name="thinking_face", timestamp=event["ts"])
        except Exception:
            pass

        await say(text=response_text, thread_ts=thread_ts)

    return app


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    if not get_active_llm():
        log.error("No active LLM provider configured. Add an API key in Graphene → LLM Providers.")
        return

    providers = [p for p in load_providers() if p["type"] == "slack" and p.get("botToken")]
    if not providers:
        log.error("No Slack providers configured. Add one in Graphene → Chat Providers.")
        return

    handlers = []
    for provider in providers:
        log.info(f"Connecting to Slack workspace: {provider['name']} ({provider.get('workspaceId', 'unknown')})")
        app = build_app(provider)
        handler = AsyncSocketModeHandler(app, provider["appToken"])
        handlers.append(handler)

    await asyncio.gather(*[h.start_async() for h in handlers])


if __name__ == "__main__":
    asyncio.run(main())
