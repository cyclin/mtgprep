import os
import json
import math
import asyncio
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import httpx
import requests
from bs4 import BeautifulSoup
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
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")  # For web search capabilities

if not OPENAI_API_KEY:
    # We'll raise at runtime if someone actually calls the endpoint, but keep server booting.
    pass

DEV_MESSAGE = """
You are CROmetrics’ Executive Meeting Copilot.
Goal: produce a decisive, 1–2 page meeting brief (≈800–1200 words) that helps us win trust and drive next steps.
Audience: CROmetrics execs and account leaders.
Tone: direct, skeptical, candid. No filler.

Guardrails
- Use only the provided context (Slack excerpts, HubSpot fields, purpose). If a fact is missing, mark it **Unknown** and move on.
- Ground claims in evidence. When referencing Slack, optionally cite inline like: `[2025-08-21T15:42Z @Jane]`.
- Prefer bullets over prose; keep lines tight; no paragraph longer than 3 lines.
- If there’s ambiguity, offer **one** best assumption and label it as such.

Output (Markdown, use these headings exactly)
1) TL;DR  
   • 5–7 bullets capturing the thesis, current state, and the single biggest risk/opportunity.
2) Meeting Objectives  
   • Convert the stated purpose into 2–5 measurable objectives (what success looks like today).  
3) Account Snapshot  
   • Stage/health, open deals or initiatives, decision cadence, blockers, last 2–3 notable decisions.  
4) Attendee One-Pagers  
   • For each attendee: Role & incentives • What they likely care about • Prior interactions (from context) • Likely objections • How to win them • LinkedIn link.
5) What’s New in Slack  
   • 3–6 themes with 1–2 bullets each; include 1–3 evidence citations per theme using the timestamp format above.  
6) Hypotheses & Win Themes  
   • 3–5 crisp hypotheses about what will move the needle; tie each to evidence or a clearly labeled assumption.
7) Smart Questions to Ask  
   • 5–10 targeted questions that unlock decisions or de-risk execution.
8) Risks & Counters  
   • Bullet pairs: **Risk → Countermove** (keep tactical and realistic).
9) 14-Day Action Plan  
   • Owner • Action • Due date. Prioritize for impact and sequencing.  
10) Validation Checklist  
   • 5–8 facts to confirm before/at the meeting.

Internal quality check (perform silently; do not print):
- Are objectives aligned with the purpose and realistically testable in this meeting?
- Does every claim map to evidence or a labeled assumption?
- Are questions and actions sufficient to advance by at least one stage?
- Are risks concrete and paired with feasible counters?
- Did you avoid generic advice and repetition?  
Then output only the final brief.
"""

DEFAULT_USER_PROMPT = """
Create an executive meeting brief that satisfies the Developer spec above.
Use the ATTENDEES, ACCOUNT CONTEXT, and RECENT SLACK provided. Prioritize what's actionable in the next 14 days.
Base every claim on the given context; if not present, mark as **Unknown**. Offer at most one labeled assumption when necessary.
Cite Slack evidence inline as `[ISO8601Z @name]` when helpful. End with the Validation Checklist.
"""

BD_DEV_MESSAGE = """
You are CROmetrics' External Business Development Meeting Intelligence Agent.
Goal: produce a comprehensive, strategic intelligence report (≈1500–2000 words) that positions us to win external BD meetings.
Audience: CROmetrics executives preparing for high-stakes external meetings.
Tone: analytical, strategic, confident. Focus on actionable intelligence.

Guardrails
- Use only the provided research context. If information is missing, mark it **Unknown** and suggest research priorities.
- Ground all claims in evidence from the research provided. Cite sources when helpful.
- Prefer structured analysis over narrative; use bullets and clear sections.
- When making strategic assumptions, label them clearly and provide reasoning.

Output (Markdown, use these headings exactly)
1) Executive Summary
   • 3-5 bullets capturing the key strategic opportunity, their current state, and our positioning advantage.
2) Target Company Intelligence
   • Business model, recent performance, strategic priorities, digital transformation initiatives.
3) Key Executive Profiles
   • For each key attendee: Background, career progression, likely priorities, decision-making style, previous company experience.
4) Competitive Landscape Analysis
   • How they compare to industry leaders, gaps we've identified, transformation maturity.
5) Strategic Opportunity Assessment
   • Specific areas where CROmetrics can add value, backed by evidence from research.
6) Meeting Objectives & Success Metrics
   • What we need to accomplish, how to measure meeting success, next steps to secure.
7) Key Questions to Ask
   • Strategic questions that demonstrate our expertise and uncover decision criteria.
8) Potential Objections & Responses
   • Likely pushback and how to address it, competitive threats to acknowledge.
9) Follow-up Action Plan
   • Specific next steps, timeline, and deliverables to propose.
10) Research Validation Needed
    • Facts to confirm, additional research priorities, intelligence gaps to fill.

Quality check (perform silently):
- Does the analysis demonstrate deep understanding of their business challenges?
- Are our value propositions specific and differentiated?
- Do questions and recommendations reflect senior-level strategic thinking?
- Are we positioned as consultative partners, not just vendors?
"""

BD_DEFAULT_PROMPT = """
Create a strategic business development intelligence report using the research provided below.
Focus on identifying specific opportunities where CROmetrics can drive measurable business impact.
Base all analysis on the research context provided. Mark gaps as **Unknown** and prioritize additional research needs.
Position CROmetrics as the strategic partner who understands their business and can accelerate their transformation goals.
"""

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
# BD Research helpers
###############################################

async def web_search(query: str, num_results: int = 10) -> List[Dict[str, Any]]:
    """Perform web search using Serper API (Google Search)."""
    if not SERPER_API_KEY:
        return [{"title": "Web search unavailable", "snippet": "SERPER_API_KEY not configured", "link": ""}]
    
    headers = {
        "X-API-KEY": SERPER_API_KEY,
        "Content-Type": "application/json"
    }
    
    payload = {
        "q": query,
        "num": num_results
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post("https://google.serper.dev/search", 
                                       json=payload, headers=headers)
            if response.status_code == 200:
                data = response.json()
                results = []
                for item in data.get("organic", []):
                    results.append({
                        "title": item.get("title", ""),
                        "snippet": item.get("snippet", ""),
                        "link": item.get("link", "")
                    })
                return results
            else:
                return [{"title": "Search error", "snippet": f"API returned {response.status_code}", "link": ""}]
    except Exception as e:
        return [{"title": "Search failed", "snippet": str(e), "link": ""}]

async def scrape_webpage(url: str) -> Dict[str, str]:
    """Scrape content from a webpage."""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
            response = await client.get(url)
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                
                # Remove script and style elements
                for script in soup(["script", "style"]):
                    script.decompose()
                
                # Get text content
                text = soup.get_text()
                
                # Clean up text
                lines = (line.strip() for line in text.splitlines())
                chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
                text = ' '.join(chunk for chunk in chunks if chunk)
                
                # Truncate if too long
                if len(text) > 5000:
                    text = text[:5000] + "... [truncated]"
                
                return {
                    "title": soup.title.string if soup.title else "No title",
                    "content": text,
                    "url": url
                }
            else:
                return {
                    "title": "Error",
                    "content": f"Failed to fetch content: HTTP {response.status_code}",
                    "url": url
                }
    except Exception as e:
        return {
            "title": "Error",
            "content": f"Failed to scrape webpage: {str(e)}",
            "url": url
        }

async def research_company(company_name: str, executive_name: str = "") -> Dict[str, Any]:
    """Perform comprehensive company research."""
    research_results = {
        "company_overview": [],
        "recent_news": [],
        "executive_info": [],
        "financial_info": [],
        "digital_transformation": []
    }
    
    # Company overview search
    overview_query = f"{company_name} company overview business model strategy 2024"
    overview_results = await web_search(overview_query, 5)
    research_results["company_overview"] = overview_results
    
    # Recent news search
    news_query = f"{company_name} news earnings digital transformation 2024"
    news_results = await web_search(news_query, 5)
    research_results["recent_news"] = news_results
    
    # Executive research if provided
    if executive_name:
        exec_query = f"{executive_name} {company_name} background career linkedin"
        exec_results = await web_search(exec_query, 3)
        research_results["executive_info"] = exec_results
    
    # Financial/earnings search
    financial_query = f"{company_name} annual report earnings financial results 2024"
    financial_results = await web_search(financial_query, 3)
    research_results["financial_info"] = financial_results
    
    # Digital transformation focus
    digital_query = f"{company_name} digital transformation data analytics technology strategy"
    digital_results = await web_search(digital_query, 4)
    research_results["digital_transformation"] = digital_results
    
    return research_results

async def research_competitive_landscape(company_name: str, industry: str = "") -> List[Dict[str, Any]]:
    """Research competitive landscape and industry leaders."""
    if industry:
        query = f"{industry} digital transformation leaders {company_name} competitors analysis"
    else:
        query = f"{company_name} competitors industry leaders digital transformation"
    
    results = await web_search(query, 8)
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

async def ask_o3_bd(user_prompt: str, research_context: str, effort: str = "high") -> str:
    """BD-specific version of OpenAI o3 call with BD prompting."""
    client = _openai_client()
    resp = client.responses.create(
        model="o3",
        reasoning={"effort": effort},
        input=[
            {"role": "developer", "content": BD_DEV_MESSAGE},
            {"role": "user", "content": user_prompt + "\n\n" + research_context},
        ],
        max_output_tokens=4000,  # Longer for BD reports
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
  <link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600;800&family=Caveat:wght@400;700&display=swap" rel="stylesheet">
  <style>
    :root {
      /* CroMetrics Design Tokens */
      --cro-blue-800: #0F8AFF;
      --cro-blue-700: #2996FF;
      --cro-blue-500: #399EFF;
      --cro-blue-400: #61B1FF;
      --cro-blue-200: #9CCEFF;
      --cro-blue-100: #E0F0FF;
      --cro-green-700: #509A6A;
      --cro-green-600: #56A471;
      --cro-green-500: #57A773;
      --cro-green-400: #79B98F;
      --cro-green-200: #ABD3B9;
      --cro-green-100: #DEEDE3;
      --cro-purple-800: #484D6D;
      --cro-purple-700: #6D718A;
      --cro-purple-400: #A3A6B6;
      --cro-plat-400: #D5DDD9;
      --cro-plat-300: #E3E8E6;
      --cro-plat-100: #F4F6F5;
      --cro-yellow-700: #C7870A;
      --cro-yellow-600: #F5B841;
      --cro-yellow-500: #F7C667;
      --cro-yellow-400: #FADCA0;
      --cro-yellow-100: #FCEDCF;
      --cro-red-600: #EB0000;
      --cro-red-500: #FF0000;
      --cro-red-300: #FFD6D6;
      --cro-soft-black-700: #2F2B2F;
      --cro-white: #FFFFFF;
      --radius: 1.5rem;
    }

    body{
      font-family: 'Montserrat', system-ui, -apple-system, sans-serif;
      margin: 0;
      padding: 2rem;
      background: var(--cro-plat-100);
      color: var(--cro-soft-black-700);
      font-size: 16px;
      line-height: 1.6;
    }

    .nav-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 2rem;
    }

    .nav-links {
      display: flex;
      gap: 1rem;
    }

    .nav-links a {
      font-family: 'Montserrat', sans-serif;
      font-weight: 600;
      text-decoration: none;
      color: var(--cro-blue-700);
      padding: 0.5rem 1rem;
      border-radius: 8px;
      transition: all 0.2s;
    }

    .nav-links a:hover {
      background: var(--cro-blue-100);
    }

    .nav-links a.active {
      background: var(--cro-blue-700);
      color: var(--cro-white);
    }

    h1{
      font-family: 'Montserrat', sans-serif;
      font-weight: 800;
      font-size: 2.5rem;
      color: var(--cro-soft-black-700);
      margin: 0;
      text-align: center;
    }

    label{
      font-family: 'Montserrat', sans-serif;
      display: block;
      font-weight: 600;
      margin: 1rem 0 0.5rem 0;
      color: var(--cro-soft-black-700);
      font-size: 0.9rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }

    select, input, textarea{
      font-family: 'Montserrat', sans-serif;
      font-size: 1rem;
      padding: 0.75rem 1rem;
      border: 1px solid var(--cro-plat-300);
      border-radius: 12px;
      background: var(--cro-white);
      color: var(--cro-soft-black-700);
      transition: all 0.2s;
      width: 100%;
      box-sizing: border-box;
    }

    select:focus, input:focus, textarea:focus{
      outline: none;
      border-color: var(--cro-blue-700);
      box-shadow: 0 0 0 3px var(--cro-blue-100);
    }

    textarea{
      width: 100%;
      min-height: 160px;
      resize: vertical;
      font-family: 'Montserrat', sans-serif;
      line-height: 1.5;
    }

    button{
      font-family: 'Montserrat', sans-serif;
      font-weight: 600;
      font-size: 1rem;
      padding: 0.75rem 2rem;
      border: none;
      border-radius: var(--radius);
      background: var(--cro-blue-700);
      color: var(--cro-white);
      cursor: pointer;
      transition: all 0.2s;
      box-shadow: 0 1px 3px rgba(0,0,0,0.1);
    }

    button:hover{
      background: var(--cro-blue-800);
      transform: translateY(-1px);
      box-shadow: 0 2px 6px rgba(0,0,0,0.15);
    }

    button:active{
      transform: translateY(0);
    }

    button[disabled]{
      opacity: 0.5;
      cursor: not-allowed;
      transform: none;
    }

    .row{
      display: flex;
      gap: 1.5rem;
      flex-wrap: wrap;
      align-items: flex-end;
      margin-bottom: 1rem;
    }

    .row > div{
      flex: 1;
      min-width: 250px;
      display: flex;
      flex-direction: column;
    }

    .card{
      background: var(--cro-white);
      border: 1px solid var(--cro-plat-300);
      border-radius: var(--radius);
      padding: 2rem;
      margin: 1rem 0;
      box-shadow: 0 1px 3px rgba(0,0,0,0.1);
    }

    #out{
      background: var(--cro-white);
      border: 1px solid var(--cro-plat-300);
      padding: 2rem;
      border-radius: var(--radius);
      line-height: 1.6;
      font-family: 'Montserrat', sans-serif;
      box-shadow: 0 1px 3px rgba(0,0,0,0.1);
      margin-top: 2rem;
    }

    #out h1, #out h2, #out h3{
      font-family: 'Montserrat', sans-serif;
      margin-top: 2rem;
      margin-bottom: 1rem;
      color: var(--cro-soft-black-700);
    }

    #out h1{
      font-size: 2rem;
      font-weight: 800;
      border-bottom: 2px solid var(--cro-plat-300);
      padding-bottom: 0.75rem;
      color: var(--cro-blue-700);
    }

    #out h2{
      font-size: 1.5rem;
      font-weight: 700;
      color: var(--cro-soft-black-700);
    }

    #out h3{
      font-size: 1.25rem;
      font-weight: 600;
      color: var(--cro-purple-700);
    }

    #out p{
      margin: 1rem 0;
      color: var(--cro-soft-black-700);
    }

    #out ul, #out ol{
      margin: 1rem 0;
      padding-left: 2rem;
    }

    #out li{
      margin: 0.5rem 0;
      color: var(--cro-soft-black-700);
    }

    #out strong{
      font-weight: 700;
      color: var(--cro-soft-black-700);
    }

    #out code{
      background: var(--cro-plat-100);
      padding: 0.25rem 0.5rem;
      border-radius: 6px;
      font-family: 'Monaco', 'Menlo', monospace;
      font-size: 0.9rem;
      color: var(--cro-soft-black-700);
    }

    #out pre{
      background: var(--cro-plat-100);
      padding: 1.5rem;
      border-radius: 12px;
      overflow-x: auto;
      margin: 1.5rem 0;
      border: 1px solid var(--cro-plat-300);
    }

    #out blockquote{
      border-left: 4px solid var(--cro-blue-400);
      padding-left: 1.5rem;
      margin: 1.5rem 0;
      font-style: italic;
      color: var(--cro-purple-700);
    }

    .muted{
      color: var(--cro-purple-400);
      font-size: 0.875rem;
      font-family: 'Montserrat', sans-serif;
    }

    /* Responsive Design */
    @media (max-width: 768px) {
      body { 
        padding: 1rem; 
      }
      
      .row { 
        flex-direction: column; 
        gap: 1rem; 
      }
      
      .row > div { 
        min-width: auto; 
      }
      
      h1 { 
        font-size: 2rem; 
      }
      
      .card {
        padding: 1.5rem;
      }
    }
  </style>
</head>
<body>
  <div class="nav-header">
    <h1>Executive Meeting Brief Generator</h1>
    <div class="nav-links">
      <a href="/" class="active">Internal Meetings</a>
      <a href="/bd">BD Meetings</a>
    </div>
  </div>
  <div class="card">
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

    <div class="row">
      <div>
        <label for="attendees">Attendee emails (comma-separated)</label>
        <input id="attendees" type="text" placeholder="alex@client.com, pat@client.com" />
        <div class="muted" style="margin-top: 0.5rem;">HubSpot Private App token must be set to enrich attendees; otherwise this is ignored.</div>
      </div>
      <div>
        <label for="purpose">Meeting purpose</label>
        <input id="purpose" type="text" placeholder="Discovery for Q4 upsell" />
      </div>
    </div>

    <label for="prompt">Instruction to the model</label>
    <textarea id="prompt">Create an executive meeting brief that satisfies the Developer spec above.
Use the ATTENDEES, ACCOUNT CONTEXT, and RECENT SLACK provided. Prioritize what's actionable in the next 14 days.
Base every claim on the given context; if not present, mark as **Unknown**. Offer at most one labeled assumption when necessary.
Cite Slack evidence inline as `[ISO8601Z @name]` when helpful. End with the Validation Checklist.</textarea>

    <div class="row" style="margin-top: 1.5rem;">
      <button id="run">Run</button>
      <div id="status" class="muted" style="align-self: center; margin-left: 1rem;"></div>
    </div>
  </div>

  <h3 style="margin-top: 2rem; margin-bottom: 1rem; font-family: 'Montserrat', sans-serif; color: var(--cro-soft-black-700);">Output</h3>
  <div id="out">(result will appear here)</div>

  <script>
    const channelSel = document.getElementById('channel');
    const out = document.getElementById('out');
    const statusEl = document.getElementById('status');

    function parseMarkdown(text) {
      // Simple markdown parser to avoid regex escaping issues
      let lines = text.split('\\n');
      let html = [];
      let inList = false;
      
      for (let line of lines) {
        if (line.startsWith('### ')) {
          html.push('<h3>' + line.substring(4) + '</h3>');
        } else if (line.startsWith('## ')) {
          html.push('<h2>' + line.substring(3) + '</h2>');
        } else if (line.startsWith('# ')) {
          html.push('<h1>' + line.substring(2) + '</h1>');
        } else if (line.startsWith('- ') || line.startsWith('* ')) {
          if (!inList) {
            html.push('<ul>');
            inList = true;
          }
          html.push('<li>' + line.substring(2) + '</li>');
        } else if (line.match(/^\\d+\\. /)) {
          if (!inList) {
            html.push('<ol>');
            inList = true;
          }
          html.push('<li>' + line.replace(/^\\d+\\. /, '') + '</li>');
        } else {
          if (inList) {
            html.push('</ul>');
            inList = false;
          }
          if (line.trim()) {
            line = line.replace(/\\*\\*(.*?)\\*\\*/g, '<strong>$1</strong>');
            line = line.replace(/\\*(.*?)\\*/g, '<em>$1</em>');
            line = line.replace(/`(.*?)`/g, '<code>$1</code>');
            html.push('<p>' + line + '</p>');
          }
        }
      }
      if (inList) html.push('</ul>');
      return html.join('');
    }

    async function loadChannels(){
      statusEl.textContent = 'Loading Slack channels…';
      console.log('Starting to load channels...');
      console.log('channelSel element:', channelSel);
      try{
        const r = await fetch('/api/channels');
        console.log('Fetch response status:', r.status, r.ok);
        if(!r.ok){ throw new Error(await r.text()); }
        const data = await r.json();
        console.log('Channels data received:', data);
        channelSel.innerHTML = '';
        data.channels.forEach(c => {
          const opt = document.createElement('option');
          opt.value = c.id;
          opt.textContent = (c.is_private ? '🔒 ' : '# ') + (c.name || c.id);
          channelSel.appendChild(opt);
        });
        console.log('Added', data.channels.length, 'channels to dropdown');
        statusEl.textContent = '';
      }catch(e){
        console.error('Channel loading error:', e);
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

BD_INDEX_HTML = """
<!doctype html>
<html>
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>BD Meeting Intelligence Generator</title>
  <link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;500;600;800&family=Caveat:wght@400;700&display=swap" rel="stylesheet">
  <style>
    :root {
      /* CroMetrics Design Tokens */
      --cro-blue-800: #0F8AFF;
      --cro-blue-700: #2996FF;
      --cro-blue-500: #399EFF;
      --cro-blue-400: #61B1FF;
      --cro-blue-200: #9CCEFF;
      --cro-blue-100: #E0F0FF;
      --cro-green-700: #509A6A;
      --cro-green-600: #56A471;
      --cro-green-500: #57A773;
      --cro-green-400: #79B98F;
      --cro-green-200: #ABD3B9;
      --cro-green-100: #DEEDE3;
      --cro-purple-800: #484D6D;
      --cro-purple-700: #6D718A;
      --cro-purple-400: #A3A6B6;
      --cro-plat-400: #D5DDD9;
      --cro-plat-300: #E3E8E6;
      --cro-plat-100: #F4F6F5;
      --cro-yellow-700: #C7870A;
      --cro-yellow-600: #F5B841;
      --cro-yellow-500: #F7C667;
      --cro-yellow-400: #FADCA0;
      --cro-yellow-100: #FCEDCF;
      --cro-red-600: #EB0000;
      --cro-red-500: #FF0000;
      --cro-red-300: #FFD6D6;
      --cro-soft-black-700: #2F2B2F;
      --cro-white: #FFFFFF;
      --radius: 1.5rem;
    }

    body{
      font-family: 'Montserrat', system-ui, -apple-system, sans-serif;
      margin: 0;
      padding: 2rem;
      background: var(--cro-plat-100);
      color: var(--cro-soft-black-700);
      font-size: 16px;
      line-height: 1.6;
    }

    .nav-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 2rem;
    }

    .nav-links {
      display: flex;
      gap: 1rem;
    }

    .nav-links a {
      font-family: 'Montserrat', sans-serif;
      font-weight: 600;
      text-decoration: none;
      color: var(--cro-blue-700);
      padding: 0.5rem 1rem;
      border-radius: 8px;
      transition: all 0.2s;
    }

    .nav-links a:hover {
      background: var(--cro-blue-100);
    }

    .nav-links a.active {
      background: var(--cro-blue-700);
      color: var(--cro-white);
    }

    h1{
      font-family: 'Montserrat', sans-serif;
      font-weight: 800;
      font-size: 2.5rem;
      color: var(--cro-soft-black-700);
      margin: 0;
      text-align: center;
    }

    label{
      font-family: 'Montserrat', sans-serif;
      display: block;
      font-weight: 600;
      margin: 1rem 0 0.5rem 0;
      color: var(--cro-soft-black-700);
      font-size: 0.9rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }

    select, input, textarea{
      font-family: 'Montserrat', sans-serif;
      font-size: 1rem;
      padding: 0.75rem 1rem;
      border: 1px solid var(--cro-plat-300);
      border-radius: 12px;
      background: var(--cro-white);
      color: var(--cro-soft-black-700);
      transition: all 0.2s;
      width: 100%;
      box-sizing: border-box;
    }

    select:focus, input:focus, textarea:focus{
      outline: none;
      border-color: var(--cro-blue-700);
      box-shadow: 0 0 0 3px var(--cro-blue-100);
    }

    textarea{
      width: 100%;
      min-height: 120px;
      resize: vertical;
      font-family: 'Montserrat', sans-serif;
      line-height: 1.5;
    }

    button{
      font-family: 'Montserrat', sans-serif;
      font-weight: 600;
      font-size: 1rem;
      padding: 0.75rem 2rem;
      border: none;
      border-radius: var(--radius);
      background: var(--cro-blue-700);
      color: var(--cro-white);
      cursor: pointer;
      transition: all 0.2s;
      box-shadow: 0 1px 3px rgba(0,0,0,0.1);
    }

    button:hover{
      background: var(--cro-blue-800);
      transform: translateY(-1px);
      box-shadow: 0 2px 6px rgba(0,0,0,0.15);
    }

    button:active{
      transform: translateY(0);
    }

    button[disabled]{
      opacity: 0.5;
      cursor: not-allowed;
      transform: none;
    }

    .row{
      display: flex;
      gap: 1.5rem;
      flex-wrap: wrap;
      align-items: flex-end;
      margin-bottom: 1rem;
    }

    .row > div{
      flex: 1;
      min-width: 250px;
      display: flex;
      flex-direction: column;
    }

    .card{
      background: var(--cro-white);
      border: 1px solid var(--cro-plat-300);
      border-radius: var(--radius);
      padding: 2rem;
      margin: 1rem 0;
      box-shadow: 0 1px 3px rgba(0,0,0,0.1);
    }

    #out{
      background: var(--cro-white);
      border: 1px solid var(--cro-plat-300);
      padding: 2rem;
      border-radius: var(--radius);
      line-height: 1.6;
      font-family: 'Montserrat', sans-serif;
      box-shadow: 0 1px 3px rgba(0,0,0,0.1);
      margin-top: 2rem;
    }

    #out h1, #out h2, #out h3{
      font-family: 'Montserrat', sans-serif;
      margin-top: 2rem;
      margin-bottom: 1rem;
      color: var(--cro-soft-black-700);
    }

    #out h1{
      font-size: 2rem;
      font-weight: 800;
      border-bottom: 2px solid var(--cro-plat-300);
      padding-bottom: 0.75rem;
      color: var(--cro-blue-700);
    }

    #out h2{
      font-size: 1.5rem;
      font-weight: 700;
      color: var(--cro-soft-black-700);
    }

    #out h3{
      font-size: 1.25rem;
      font-weight: 600;
      color: var(--cro-purple-700);
    }

    #out p{
      margin: 1rem 0;
      color: var(--cro-soft-black-700);
    }

    #out ul, #out ol{
      margin: 1rem 0;
      padding-left: 2rem;
    }

    #out li{
      margin: 0.5rem 0;
      color: var(--cro-soft-black-700);
    }

    #out strong{
      font-weight: 700;
      color: var(--cro-soft-black-700);
    }

    #out code{
      background: var(--cro-plat-100);
      padding: 0.25rem 0.5rem;
      border-radius: 6px;
      font-family: 'Monaco', 'Menlo', monospace;
      font-size: 0.9rem;
      color: var(--cro-soft-black-700);
    }

    .muted{
      color: var(--cro-purple-400);
      font-size: 0.875rem;
      font-family: 'Montserrat', sans-serif;
    }

    .research-progress {
      background: var(--cro-blue-100);
      border: 1px solid var(--cro-blue-200);
      border-radius: 12px;
      padding: 1rem;
      margin: 1rem 0;
      font-family: 'Montserrat', sans-serif;
    }

    .research-step {
      margin: 0.5rem 0;
      color: var(--cro-blue-800);
    }

    /* Responsive Design */
    @media (max-width: 768px) {
      body { 
        padding: 1rem; 
      }
      
      .row { 
        flex-direction: column; 
        gap: 1rem; 
      }
      
      .row > div { 
        min-width: auto; 
      }
      
      h1 { 
        font-size: 2rem; 
      }
      
      .card {
        padding: 1.5rem;
      }
    }
  </style>
</head>
<body>
  <div class="nav-header">
    <h1>BD Meeting Intelligence</h1>
    <div class="nav-links">
      <a href="/">Internal Meetings</a>
      <a href="/bd" class="active">BD Meetings</a>
    </div>
  </div>

  <div class="card">
    <div class="row">
      <div>
        <label for="company">Target Company</label>
        <input id="company" type="text" placeholder="Chobani" />
      </div>
      <div>
        <label for="executive">Key Executive Name</label>
        <input id="executive" type="text" placeholder="Pavi Gupta" />
      </div>
      <div>
        <label for="title">Executive Title</label>
        <input id="title" type="text" placeholder="VP of Analytics and Insights" />
      </div>
    </div>

    <div class="row">
      <div>
        <label for="industry">Industry (optional)</label>
        <input id="industry" type="text" placeholder="CPG, Food & Beverage" />
      </div>
      <div>
        <label for="effort">Research Depth</label>
        <select id="effort">
          <option value="high" selected>Comprehensive</option>
          <option value="medium">Standard</option>
          <option value="low">Quick</option>
        </select>
      </div>
    </div>

    <label for="meeting_context">Meeting Context & Objectives</label>
    <textarea id="meeting_context" placeholder="External BD meeting to explore partnership opportunities. Focus on digital transformation, data analytics, and consumer insights capabilities..."></textarea>

    <label for="prompt">Research Instructions</label>
    <textarea id="prompt">Create a strategic business development intelligence report using the research provided below.
Focus on identifying specific opportunities where CROmetrics can drive measurable business impact.
Base all analysis on the research context provided. Mark gaps as **Unknown** and prioritize additional research needs.
Position CROmetrics as the strategic partner who understands their business and can accelerate their transformation goals.</textarea>

    <div class="row" style="margin-top: 1.5rem;">
      <button id="run">Generate Intelligence Report</button>
      <div id="status" class="muted" style="align-self: center; margin-left: 1rem;"></div>
    </div>
  </div>

  <div id="research-progress" class="research-progress" style="display: none;">
    <h3>Research Progress</h3>
    <div id="progress-steps"></div>
  </div>

  <h3 style="margin-top: 2rem; margin-bottom: 1rem; font-family: 'Montserrat', sans-serif; color: var(--cro-soft-black-700);">Intelligence Report</h3>
  <div id="out">(report will appear here)</div>

  <script>
    const out = document.getElementById('out');
    const statusEl = document.getElementById('status');
    const progressEl = document.getElementById('research-progress');
    const progressSteps = document.getElementById('progress-steps');

    function parseMarkdown(text) {
      // Simple markdown parser
      let lines = text.split('\\n');
      let html = [];
      let inList = false;
      
      for (let line of lines) {
        if (line.startsWith('### ')) {
          html.push('<h3>' + line.substring(4) + '</h3>');
        } else if (line.startsWith('## ')) {
          html.push('<h2>' + line.substring(3) + '</h2>');
        } else if (line.startsWith('# ')) {
          html.push('<h1>' + line.substring(2) + '</h1>');
        } else if (line.startsWith('- ') || line.startsWith('* ')) {
          if (!inList) {
            html.push('<ul>');
            inList = true;
          }
          html.push('<li>' + line.substring(2) + '</li>');
        } else if (line.match(/^\\d+\\. /)) {
          if (!inList) {
            html.push('<ol>');
            inList = true;
          }
          html.push('<li>' + line.replace(/^\\d+\\. /, '') + '</li>');
        } else {
          if (inList) {
            html.push('</ul>');
            inList = false;
          }
          if (line.trim()) {
            line = line.replace(/\\*\\*(.*?)\\*\\*/g, '<strong>$1</strong>');
            line = line.replace(/\\*(.*?)\\*/g, '<em>$1</em>');
            line = line.replace(/`(.*?)`/g, '<code>$1</code>');
            html.push('<p>' + line + '</p>');
          }
        }
      }
      if (inList) html.push('</ul>');
      return html.join('');
    }

    function updateProgress(step) {
      const stepEl = document.createElement('div');
      stepEl.className = 'research-step';
      stepEl.textContent = '✓ ' + step;
      progressSteps.appendChild(stepEl);
    }

    async function run(){
      out.textContent = '';
      statusEl.textContent = 'Starting research...';
      progressEl.style.display = 'block';
      progressSteps.innerHTML = '';
      document.getElementById('run').disabled = true;
      
      try{
        const body = {
          company_name: document.getElementById('company').value,
          executive_name: document.getElementById('executive').value,
          executive_title: document.getElementById('title').value,
          industry: document.getElementById('industry').value,
          meeting_context: document.getElementById('meeting_context').value,
          effort: document.getElementById('effort').value,
          prompt: document.getElementById('prompt').value,
        };
        
        updateProgress('Initiating company research...');
        
        const r = await fetch('/api/bd/generate', {
          method:'POST', 
          headers:{'Content-Type':'application/json'}, 
          body: JSON.stringify(body)
        });
        
        const data = await r.json();
        if(!r.ok){ throw new Error(data.detail || JSON.stringify(data)); }
        
        updateProgress('Research completed, generating report...');
        statusEl.textContent = 'Done.';
        
        const markdown = data.report_markdown || '(no output)';
        out.innerHTML = parseMarkdown(markdown);
        
        setTimeout(() => {
          progressEl.style.display = 'none';
        }, 3000);
        
      }catch(e){
        statusEl.textContent = 'Error: ' + (e && e.message ? e.message : e);
        progressEl.style.display = 'none';
      }finally{
        document.getElementById('run').disabled = false;
      }
    }

    document.getElementById('run').addEventListener('click', run);
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

@app.get("/bd", response_class=HTMLResponse)
async def bd_index() -> str:
    return BD_INDEX_HTML

@app.post("/api/bd/generate")
async def api_bd_generate(req: Request) -> JSONResponse:
    payload = await req.json()

    company_name = (payload.get("company_name") or "").strip()
    if not company_name:
        raise HTTPException(status_code=400, detail="company_name is required")

    executive_name = (payload.get("executive_name") or "").strip()
    executive_title = (payload.get("executive_title") or "").strip()
    industry = (payload.get("industry") or "").strip()
    meeting_context = (payload.get("meeting_context") or "").strip()
    effort = (payload.get("effort") or "high").lower()
    prompt = (payload.get("prompt") or BD_DEFAULT_PROMPT).strip()

    # 1) Company research
    research_data = await research_company(company_name, executive_name)
    
    # 2) Competitive landscape research
    competitive_data = await research_competitive_landscape(company_name, industry)
    
    # 3) Format research context
    research_sections = []
    
    # Company overview
    if research_data.get("company_overview"):
        research_sections.append("## Company Overview Research")
        for item in research_data["company_overview"]:
            research_sections.append(f"**{item.get('title', 'N/A')}**")
            research_sections.append(f"Source: {item.get('link', 'N/A')}")
            research_sections.append(f"{item.get('snippet', 'No snippet available')}\n")
    
    # Recent news
    if research_data.get("recent_news"):
        research_sections.append("## Recent News & Developments")
        for item in research_data["recent_news"]:
            research_sections.append(f"**{item.get('title', 'N/A')}**")
            research_sections.append(f"Source: {item.get('link', 'N/A')}")
            research_sections.append(f"{item.get('snippet', 'No snippet available')}\n")
    
    # Executive information
    if research_data.get("executive_info") and executive_name:
        research_sections.append(f"## Executive Profile: {executive_name}")
        for item in research_data["executive_info"]:
            research_sections.append(f"**{item.get('title', 'N/A')}**")
            research_sections.append(f"Source: {item.get('link', 'N/A')}")
            research_sections.append(f"{item.get('snippet', 'No snippet available')}\n")
    
    # Financial information
    if research_data.get("financial_info"):
        research_sections.append("## Financial & Performance Data")
        for item in research_data["financial_info"]:
            research_sections.append(f"**{item.get('title', 'N/A')}**")
            research_sections.append(f"Source: {item.get('link', 'N/A')}")
            research_sections.append(f"{item.get('snippet', 'No snippet available')}\n")
    
    # Digital transformation focus
    if research_data.get("digital_transformation"):
        research_sections.append("## Digital Transformation & Technology")
        for item in research_data["digital_transformation"]:
            research_sections.append(f"**{item.get('title', 'N/A')}**")
            research_sections.append(f"Source: {item.get('link', 'N/A')}")
            research_sections.append(f"{item.get('snippet', 'No snippet available')}\n")
    
    # Competitive landscape
    if competitive_data:
        research_sections.append("## Competitive Landscape Analysis")
        for item in competitive_data:
            research_sections.append(f"**{item.get('title', 'N/A')}**")
            research_sections.append(f"Source: {item.get('link', 'N/A')}")
            research_sections.append(f"{item.get('snippet', 'No snippet available')}\n")

    research_context = "\n".join(research_sections) if research_sections else "No research data available."
    
    # 4) Compose full context
    composed_context = (
        f"TARGET COMPANY: {company_name}\n"
        f"KEY EXECUTIVE: {executive_name} - {executive_title}\n"
        f"INDUSTRY: {industry or 'Not specified'}\n"
        f"MEETING CONTEXT: {meeting_context or 'Not provided'}\n\n"
        f"RESEARCH INTELLIGENCE:\n{research_context}"
    )

    # 5) Generate BD intelligence report
    report = await asyncio.wait_for(ask_o3_bd(prompt, composed_context, effort=effort), timeout=300.0)

    return JSONResponse({
        "report_markdown": report,
        "meta": {
            "company_name": company_name,
            "executive_name": executive_name,
            "research_sections": len(research_sections),
            "effort": effort,
        }
    })

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
