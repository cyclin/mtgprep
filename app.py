import os
import json
import math
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# OpenAI SDK (>= 1.40)
try:
    from openai import OpenAI  # type: ignore
except Exception as e:  # pragma: no cover
    OpenAI = None  # fallback for type checking

###############################################
# Environment & constants
###############################################
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
SLACK_TOKEN = os.getenv("SLACK_USER_TOKEN") or os.getenv("SLACK_BOT_TOKEN") or os.getenv("SLACK_TOKEN")
HUBSPOT_TOKEN = os.getenv("HUBSPOT_TOKEN") or os.getenv("HUBSPOT_PRIVATE_APP_TOKEN")
HUBSPOT_API_BASE = "https://api.hubapi.com"

if not OPENAI_API_KEY:
    # We'll raise at runtime if someone actually calls the endpoint, but keep server booting.
    pass

DEV_MESSAGE = (
    "You are CROmetrics’ Executive Meeting Copilot. Produce a hard-hitting, 1–2 page brief. "
    "Tone: concise, skeptical, candid. Identify risks early and propose concrete actions.\n"
    "Output sections (Markdown):\n"
    "1) TL;DR (5–7 bullets)\n"
    "2) Meeting Objectives (numbered)\n"
    "3) Account Snapshot (stage, health, blockers)\n"
    "4) Attendee One-Pagers (role, incentives, prior interactions, likely objections, LinkedIn link)\n"
    "5) What’s New in Slack (themes; cite [ts])\n"
    "6) Hypotheses & Win Themes\n"
    "7) Smart Questions to Ask (5–10)\n"
    "8) Risks & Counters\n"
    "9) 14-Day Action Plan (owner, date)\n"
    "If context is missing, state the gap and give the single best assumption. End with a validation checklist."
)

DEFAULT_USER_PROMPT = (
    "Produce an executive meeting brief using the sections in the developer message.\n"
    "Use the ATTENDEES, ACCOUNT CONTEXT, and RECENT SLACK below.\n"
    "Be candid about unknowns and end with a validation checklist."
)

###############################################
# FastAPI app
###############################################
app = FastAPI(title="Executive Meeting Brief Generator", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"]
    ,
    allow_headers=["*"]
)

###############################################
# Utilities
###############################################

def _openai_client() -> Any:
    if OpenAI is None:
        raise HTTPException(status_code=500, detail="OpenAI SDK not available")
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=400, detail="OPENAI_API_KEY not configured")
    return OpenAI(api_key=OPENAI_API_KEY)

# Simple in-memory caches for this process lifetime
_user_cache: Dict[str, Dict[str, Any]] = {}

async def _sleep_for_retry(resp: httpx.Response) -> None:
    if resp.status_code == 429:
        try:
            retry_after = int(resp.headers.get("Retry-After", "1"))
        except ValueError:
            retry_after = 1
        await asyncio.sleep(min(retry_after, 5))

###############################################
# Slack helpers
###############################################

SLACK_API_BASE = "https://slack.com/api"

async def slack_call(method: str, params: Dict[str, Any]) -> Dict[str, Any]:
    if not SLACK_TOKEN:
        raise HTTPException(status_code=400, detail="Slack token not configured")
    headers = {"Authorization": f"Bearer {SLACK_TOKEN}"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        url = f"{SLACK_API_BASE}/{method}"
        while True:
            resp = await client.get(url, params=params, headers=headers)
            if resp.status_code == 429:
                await _sleep_for_retry(resp)
                continue
            data = resp.json()
            if not data.get("ok"):
                # Return early for visibility; include a snippet of error
                raise HTTPException(status_code=400, detail=f"Slack error in {method}: {data.get('error')}")
            return data

async def list_channels(limit: int = 200) -> List[Dict[str, Any]]:
    params = {
        "exclude_archived": True,
        "limit": min(limit, 1000),
        "types": "public_channel,private_channel",
    }
    channels: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    for _ in range(5):  # basic pagination loop
        if cursor:
            params["cursor"] = cursor
        data = await slack_call("conversations.list", params)
        channels.extend(data.get("channels", []))
        cursor = data.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    return channels

async def slack_user_name(user_id: str) -> str:
    if user_id in _user_cache:
        return _user_cache[user_id].get("name") or _user_cache[user_id].get("real_name", user_id)
    data = await slack_call("users.info", {"user": user_id})
    prof = data.get("user", {})
    name = prof.get("profile", {}).get("display_name") or prof.get("real_name") or user_id
    _user_cache[user_id] = {"name": name}
    return name

async def fetch_channel_context(
    channel_id: str,
    *,
    lookback_days: int = 14,
    max_messages: int = 300,
    resolve_names: bool = True,
    expand_threads: bool = True,
) -> Tuple[str, int]:
    """Return a string block of recent Slack messages (and optionally thread replies)
    formatted as bullet lines with timestamps. Also return actual lookback_days used.
    """
    oldest_ts = math.floor((datetime.now(timezone.utc) - timedelta(days=lookback_days)).timestamp())

    params = {
        "channel": channel_id,
        "oldest": oldest_ts,
        "limit": 200,
        "inclusive": True,
    }
    messages: List[Dict[str, Any]] = []

    # Paginate conversations.history
    cursor: Optional[str] = None
    for _ in range(10):
        if cursor:
            params["cursor"] = cursor
        data = await slack_call("conversations.history", params)
        batch = data.get("messages", [])
        messages.extend(batch)
        cursor = data.get("response_metadata", {}).get("next_cursor")
        if not cursor or len(messages) >= max_messages:
            break

    # Optionally expand threads
    if expand_threads:
        # Only expand the most recent threads to keep latency predictable
        thread_parents = [m for m in messages if m.get("thread_ts") and m.get("ts") == m.get("thread_ts")]
        thread_parents = sorted(thread_parents, key=lambda x: float(x.get("ts", 0.0)), reverse=True)[:20]
        for parent in thread_parents:
            ts = parent.get("ts")
            if not ts:
                continue
            data = await slack_call("conversations.replies", {"channel": channel_id, "ts": ts, "limit": 100})
            replies = data.get("messages", [])[1:]  # skip the parent itself
            # Attach a synthetic field so we can group in rendering
            parent.setdefault("_replies", replies)

    # Render to bullet lines
    lines: List[str] = []
    count = 0
    for m in sorted(messages, key=lambda x: float(x.get("ts", 0.0))):
        if count >= max_messages:
            break
        ts = m.get("ts")
        if not ts:
            continue
        # Build prefix with timestamp and optional username
        dt = datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
        prefix = f"[{dt}]"
        if resolve_names and m.get("user"):
            try:
                uname = await slack_user_name(m["user"])  # resolves & caches
                prefix += f" {uname}:"
            except Exception:
                prefix += f" {m.get('user')}:"
        text = m.get("text") or ""
        if text:
            lines.append(f"• {prefix} {text}")
            count += 1
        # Include replies indented
        for r in m.get("_replies", []) or []:
            rts = r.get("ts")
            if not rts:
                continue
            rdt = datetime.fromtimestamp(float(rts), tz=timezone.utc).isoformat()
            rprefix = f"[{rdt}]"
            if resolve_names and r.get("user"):
                try:
                    runame = await slack_user_name(r["user"])  # cache hit likely
                    rprefix += f" {runame}:"
                except Exception:
                    rprefix += f" {r.get('user')}:"
            rtext = r.get("text") or ""
            if rtext:
                lines.append(f"    ◦ {rprefix} {rtext}")
                count += 1
            if count >= max_messages:
                break
        if count >= max_messages:
            break

    context_block = "\n".join(lines) if lines else "(no recent Slack messages in window)"
    return context_block, lookback_days

###############################################
# HubSpot helpers
###############################################

async def hubspot_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if not HUBSPOT_TOKEN:
        # Make this non-fatal; the app can run without HubSpot if needed
        raise HTTPException(status_code=400, detail="HubSpot token not configured")
    headers = {"Authorization": f"Bearer {HUBSPOT_TOKEN}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(f"{HUBSPOT_API_BASE}{path}", json=payload, headers=headers)
        if resp.status_code >= 400:
            raise HTTPException(status_code=400, detail=f"HubSpot error: {resp.text[:300]}")
        return resp.json()

async def fetch_contacts_by_email(emails: List[str]) -> List[Dict[str, Any]]:
    """Look up each email individually via CRM Search. Expects a custom contact property
    'linkedin_url' (type URL). Returns a simplified list of properties for each found contact.
    """
    results: List[Dict[str, Any]] = []
    props = [
        "email",
        "firstname",
        "lastname",
        "jobtitle",
        "company",
        "lifecyclestage",
        "linkedin_url",
        "hs_object_id",
    ]
    for email in {e.strip().lower() for e in emails if e and e.strip()}:
        payload = {
            "filterGroups": [{"filters": [{"propertyName": "email", "operator": "EQ", "value": email}]}],
            "properties": props,
            "limit": 1,
        }
        data = await hubspot_post("/crm/v3/objects/contacts/search", payload)
        for row in data.get("results", []):
            props_row = row.get("properties", {})
            props_row["_id"] = row.get("id")
            results.append(props_row)
    return results

###############################################
# OpenAI (o3) call
###############################################

async def ask_o3(user_prompt: str, composed_context: str, effort: str = "high") -> str:
    client = _openai_client()
    resp = client.responses.create(
        model="o3",
        reasoning={"effort": effort},
        input=[
            {"role": "developer", "content": DEV_MESSAGE},
            {"role": "user", "content": user_prompt + "\n\n" + composed_context},
        ],
        max_output_tokens=3000,
    )
    # The SDK exposes a convenience property; fall back if not present.
    text = getattr(resp, "output_text", None)
    if isinstance(text, str) and text.strip():
        return text
    # Fallback: assemble from content parts
    try:
        parts = []
        for item in getattr(resp, "output", []) or []:
            if isinstance(item, dict):
                for c in item.get("content", []) or []:
                    if c.get("type") == "output_text" and c.get("text"):
                        parts.append(c["text"])            
        return "".join(parts) or json.dumps(resp.model_dump() if hasattr(resp, "model_dump") else resp, indent=2)
    except Exception:
        return "(No text output received from model)"

###############################################
# HTML front-end (simple, self-contained)
###############################################

INDEX_HTML = """
<!doctype html>
<html>
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Executive Meeting Brief Generator</title>
  <style>
    body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; margin:24px;}
    h1{margin:0 0 16px 0}
    .row{display:flex; gap:12px; align-items:flex-end; flex-wrap:wrap;}
    label{display:block; font-size:12px; color:#444; margin-bottom:4px}
    input, select, textarea{padding:8px; border:1px solid #bbb; border-radius:8px; font:inherit}
    textarea{width:100%; height:160px}
    button{padding:10px 14px; border-radius:8px; border:0; background:#111; color:white; cursor:pointer}
    button[disabled]{opacity:0.5; cursor:not-allowed}
    #out{border:1px dashed #aaa; padding:12px; border-radius:8px; margin-top:16px; line-height:1.6}
    #out h1, #out h2, #out h3{margin-top:20px; margin-bottom:10px; color:#333}
    #out h1{font-size:1.5em; border-bottom:2px solid #333}
    #out h2{font-size:1.3em; border-bottom:1px solid #666}
    #out h3{font-size:1.1em}
    #out ul, #out ol{margin:10px 0; padding-left:20px}
    #out li{margin:5px 0}
    #out p{margin:10px 0}
    #out strong{font-weight:600; color:#222}
    #out code{background:#f5f5f5; padding:2px 4px; border-radius:3px; font-family:monospace}
    #out pre{background:#f5f5f5; padding:10px; border-radius:5px; overflow-x:auto; margin:10px 0}
    #out blockquote{border-left:4px solid #ddd; padding-left:16px; margin:10px 0; font-style:italic}
    .muted{color:#666; font-size:12px}
  </style>
</head>
<body>
  <h1>Executive Meeting Brief Generator</h1>
  <div class="row">
    <div>
      <label for="channel">Slack channel</label>
      <select id="channel"></select>
    </div>
    <div>
      <label for="limit">Max messages</label>
      <input id="limit" type="number" value="300" min="20" max="1000" />
    </div>
    <div>
      <label for="days">Lookback days</label>
      <input id="days" type="number" value="14" min="1" max="90" />
    </div>
    <div>
      <label for="effort">Reasoning effort</label>
      <select id="effort">
        <option value="high" selected>high</option>
        <option value="medium">medium</option>
        <option value="low">low</option>
      </select>
    </div>
  </div>

  <div class="row" style="margin-top:12px">
    <div style="flex:1; min-width:320px">
      <label for="attendees">Attendee emails (comma-separated)</label>
      <input id="attendees" type="text" placeholder="alex@client.com, pat@client.com" style="width:100%" />
      <div class="muted">HubSpot Private App token must be set to enrich attendees; otherwise this is ignored.</div>
    </div>
    <div style="flex:1; min-width:320px">
      <label for="purpose">Meeting purpose</label>
      <input id="purpose" type="text" placeholder="Discovery for Q4 upsell" style="width:100%" />
    </div>
  </div>

  <div style="margin-top:12px">
    <label for="prompt">Instruction to the model</label>
    <textarea id="prompt">Produce an executive meeting brief using the sections in the developer message.
Use the ATTENDEES, ACCOUNT CONTEXT, and RECENT SLACK below.
Be candid about unknowns and end with a validation checklist.</textarea>
  </div>

  <div style="margin-top:12px" class="row">
    <button id="run">Run</button>
    <div id="status" class="muted"></div>
  </div>

  <div id="out"></div>

  <script>
    const channelSel = document.getElementById('channel');
    const out = document.getElementById('out');
    const statusEl = document.getElementById('status');

    function parseMarkdown(text) {
      return text
        .replace(/^### (.*$)/gim, '<h3>$1</h3>')
        .replace(/^## (.*$)/gim, '<h2>$1</h2>')
        .replace(/^# (.*$)/gim, '<h1>$1</h1>')
        .replace(/^\* (.*$)/gim, '<li>$1</li>')
        .replace(/^- (.*$)/gim, '<li>$1</li>')
        .replace(/^\d+\. (.*$)/gim, '<li>$1</li>')
        .replace(/\*\*(.*?)\*\*/gim, '<strong>$1</strong>')
        .replace(/\*(.*?)\*/gim, '<em>$1</em>')
        .replace(/`(.*?)`/gim, '<code>$1</code>')
        .replace(/\n\n/gim, '</p><p>')
        .replace(/^(?!<[hlu])/gim, '<p>')
        .replace(/(<\/li>\s*)+/gim, '</li>')
        .replace(/(<li>.*<\/li>)/gims, '<ul>$1</ul>')
        .replace(/<\/ul>\s*<ul>/gim, '')
        .replace(/(<p><\/p>)/gim, '')
        .replace(/^<p>(<h[123]>)/gim, '$1')
        .replace(/(<\/h[123]>)<\/p>$/gim, '$1');
    }

    async function loadChannels(){
      statusEl.textContent = 'Loading Slack channels…';
      try{
        const r = await fetch('/api/channels');
        if(!r.ok){ throw new Error(await r.text()); }
        const data = await r.json();
        channelSel.innerHTML = '';
        data.channels.forEach(c => {
          const opt = document.createElement('option');
          opt.value = c.id;
          opt.textContent = (c.is_private ? '🔒 ' : '# ') + (c.name || c.id);
          channelSel.appendChild(opt);
        });
        statusEl.textContent = '';
      }catch(e){
        statusEl.textContent = 'Failed to load channels: ' + (e && e.message ? e.message : e);
      }
    }

    async function run(){
      out.textContent = '';
      statusEl.textContent = 'Running…';
      document.getElementById('run').disabled = true;
      try{
        const body = {
          channel_id: document.getElementById('channel').value,
          limit: parseInt(document.getElementById('limit').value||'300',10),
          lookback_days: parseInt(document.getElementById('days').value||'14',10),
          effort: document.getElementById('effort').value,
          resolve_names: true,
          prompt: document.getElementById('prompt').value,
          attendee_emails: document.getElementById('attendees').value,
          purpose: document.getElementById('purpose').value,
        };
        const r = await fetch('/api/run', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
        const data = await r.json();
        if(!r.ok){ throw new Error(data.detail || JSON.stringify(data)); }
        statusEl.textContent = 'Done.';
        const markdown = data.brief_markdown || '(no output)';
        out.innerHTML = parseMarkdown(markdown);
      }catch(e){
        statusEl.textContent = 'Error: ' + (e && e.message ? e.message : e);
      }finally{
        document.getElementById('run').disabled = false;
      }
    }

    document.getElementById('run').addEventListener('click', run);
    loadChannels();
  </script>
</body>
</html>
"""

###############################################
# Routes
###############################################

@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return INDEX_HTML

@app.get("/api/channels")
async def api_channels() -> JSONResponse:
    channels = await list_channels(limit=400)
    # Filter for channels starting with "bd-" or "internal-" and exclude archived
    filtered_channels = [
        c for c in channels 
        if not c.get("is_archived", False) and 
        c.get("name", "").startswith(("bd-", "internal-"))
    ]
    # Trim the payload for UI
    result = [
        {"id": c.get("id"), "name": c.get("name"), "is_private": bool(c.get("is_private"))}
        for c in filtered_channels
    ]
    return JSONResponse({"channels": result})

@app.post("/api/run")
async def api_run(req: Request) -> JSONResponse:
    payload = await req.json()

    channel_id = (payload.get("channel_id") or "").strip()
    if not channel_id:
        raise HTTPException(status_code=400, detail="channel_id is required")

    limit = int(payload.get("limit") or 300)
    lookback_days = int(payload.get("lookback_days") or 14)
    effort = (payload.get("effort") or "high").lower()
    resolve_names = bool(payload.get("resolve_names", True))
    prompt = (payload.get("prompt") or DEFAULT_USER_PROMPT).strip()

    attendees_raw = payload.get("attendee_emails") or ""
    attendee_emails = [e.strip() for e in attendees_raw.split(",") if e.strip()]
    purpose = (payload.get("purpose") or "").strip()

    # 1) Slack context
    context_block, actual_days = await fetch_channel_context(
        channel_id,
        lookback_days=lookback_days,
        max_messages=limit,
        resolve_names=resolve_names,
        expand_threads=True,
    )

    # 2) HubSpot enrichment (optional)
    contacts: List[Dict[str, Any]] = []
    if attendee_emails and HUBSPOT_TOKEN:
        try:
            contacts = await asyncio.wait_for(fetch_contacts_by_email(attendee_emails), timeout=30.0)
        except asyncio.TimeoutError:
            contacts = []

    attendee_block = "\n".join(
        f"- {c.get('firstname','').strip()} {c.get('lastname','').strip()} — {c.get('jobtitle','') or ''} (" \
        f"{c.get('email')})" + (f" — {c.get('linkedin_url')}" if c.get('linkedin_url') else "")
        for c in contacts
    ) or "(none provided)"

    account_block = "\n".join(
        f"• {c.get('company') or '—'} — lifecycle: {c.get('lifecyclestage') or 'n/a'}  (contact: {c.get('email')})"
        for c in contacts
    ) or "(no HubSpot context)"

    composed_context = (
        f"MEETING PURPOSE:\n{(purpose or '(not provided)')}\n\n"
        f"ATTENDEES:\n{attendee_block}\n\n"
        f"ACCOUNT CONTEXT (HubSpot):\n{account_block}\n\n"
        f"RECENT SLACK (last {actual_days} days):\n{context_block}"
    )

    # 3) OpenAI reasoning (o3)
    text = await asyncio.wait_for(ask_o3(prompt, composed_context, effort=effort), timeout=240.0)

    return JSONResponse({
        "brief_markdown": text,
        "meta": {
            "channel_id": channel_id,
            "lookback_days": actual_days,
            "messages_limit": limit,
            "attendees_found": len(contacts),
            "effort": effort,
        }
    })

###############################################
# Local dev entrypoint
###############################################
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", "3000")), reload=True)
