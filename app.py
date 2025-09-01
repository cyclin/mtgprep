import os
import json
import math
import asyncio
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse
import logging

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

###############################################
# Usage Logging
###############################################

# Create persistent logs directory (survives deployments if using persistent storage)
LOGS_DIR = os.getenv("LOGS_DIR", "/tmp/mtgprep_logs")
os.makedirs(LOGS_DIR, exist_ok=True)

# Setup usage logger
usage_logger = logging.getLogger("mtgprep_usage")
usage_logger.setLevel(logging.INFO)

# File handler for persistent logging
usage_log_file = os.path.join(LOGS_DIR, "usage.log")
file_handler = logging.FileHandler(usage_log_file, mode='a')
file_handler.setLevel(logging.INFO)

# Create formatter
formatter = logging.Formatter('%(asctime)s | %(message)s')
file_handler.setFormatter(formatter)

# Add handler to logger
if not usage_logger.handlers:
    usage_logger.addHandler(file_handler)

def log_usage(event_type: str, data: Dict[str, Any], request: Request = None):
    """Log usage events for analysis."""
    try:
        # Get client IP if request provided
        client_ip = "unknown"
        if request:
            client_ip = request.client.host if request.client else "unknown"
            # Check for forwarded IP (common in production deployments)
            forwarded_for = request.headers.get("x-forwarded-for")
            if forwarded_for:
                client_ip = forwarded_for.split(",")[0].strip()
        
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "client_ip": client_ip,
            "data": data
        }
        
        usage_logger.info(json.dumps(log_entry))
    except Exception as e:
        # Don't let logging errors break the application
        print(f"Logging error: {e}")

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
- Pay special attention to attendee profiles and tailor recommendations to their specific backgrounds and priorities.

Output (Markdown, use these headings exactly)
1) Executive Summary
   • 3-5 bullets capturing the key strategic opportunity, their current state, and our positioning advantage.
2) Target Company Intelligence
   • Business model, recent performance, strategic priorities, digital transformation initiatives.
3) Meeting Attendee Analysis
   • For each attendee: Background, career progression, likely priorities, decision-making style, LinkedIn profile insights, and how to engage them effectively.
   • Include HubSpot relationship history if available.
   • Identify the key decision-maker and influencers in the group.
4) Competitive Landscape Analysis
   • How they compare to industry leaders, gaps we've identified, transformation maturity.
5) Strategic Opportunity Assessment
   • Specific areas where CROmetrics can add value, backed by evidence from research.
   • Map opportunities to specific attendee interests and company priorities.
6) Meeting Dynamics & Strategy
   • How to navigate the group dynamic based on attendee profiles.
   • Recommended meeting flow and who to address for different topics.
   • Potential coalition-building opportunities among attendees.
7) Key Questions to Ask
   • Strategic questions that demonstrate our expertise and uncover decision criteria.
   • Personalized questions for each key attendee based on their background.
8) Potential Objections & Responses
   • Likely pushback from each attendee type and how to address it.
   • Competitive threats to acknowledge and counter.
9) Follow-up Action Plan
   • Specific next steps, timeline, and deliverables to propose.
   • Individual follow-up strategies for each attendee.
10) Research Validation Needed
    • Facts to confirm, additional research priorities, intelligence gaps to fill.
    • Missing attendee information that could impact strategy.

Quality check (perform silently):
- Does the analysis demonstrate deep understanding of their business challenges?
- Are our value propositions specific and differentiated?
- Do questions and recommendations reflect senior-level strategic thinking?
- Are we positioned as consultative partners, not just vendors?
- Have we adequately addressed the multi-attendee dynamic and personalized our approach?
"""

BD_DEFAULT_PROMPT = """
Create a strategic business development intelligence report using the research provided below.
Focus on identifying specific opportunities where CROmetrics can drive measurable business impact.
Base all analysis on the research context provided. Mark gaps as **Unknown** and prioritize additional research needs.
Position CROmetrics as the strategic partner who understands their business and can accelerate their transformation goals.

Pay special attention to the individual attendee profiles and tailor your strategic recommendations to address each person's likely priorities and concerns. Consider how the group dynamic might influence decision-making and recommend specific approaches for engaging each attendee effectively.
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

async def research_attendee_linkedin(name: str, company_name: str, title: str = "") -> Dict[str, Optional[str]]:
    """Search for attendee's LinkedIn profile URL and snippet."""
    if not name or not company_name:
        return {"url": None, "snippet": None, "title": None}
    
    # Construct search query for LinkedIn
    query_parts = [name, company_name, "linkedin"]
    if title:
        query_parts.insert(-1, title)
    
    query = " ".join(query_parts)
    
    try:
        results = await web_search(query, 5)
        
        # Look for LinkedIn URLs in the results
        for result in results:
            link = result.get('link', '')
            result_title = result.get('title', '')
            snippet = result.get('snippet', '')
            
            # Check if this is a LinkedIn profile
            if 'linkedin.com/in/' in link:
                # Verify it's likely the right person by checking name in title/snippet
                name_parts = name.lower().split()
                if len(name_parts) >= 2:
                    first_name = name_parts[0]
                    last_name = name_parts[-1]
                    
                    # Check if both first and last name appear in title or snippet
                    title_lower = result_title.lower()
                    snippet_lower = snippet.lower()
                    
                    if (first_name in title_lower or first_name in snippet_lower) and \
                       (last_name in title_lower or last_name in snippet_lower):
                        return {
                            "url": link,
                            "snippet": snippet,
                            "title": result_title
                        }
        
        return {"url": None, "snippet": None, "title": None}
    except Exception:
        return {"url": None, "snippet": None, "title": None}

async def research_attendee_background(name: str, company_name: str, title: str = "", linkedin_url: str = "") -> Dict[str, Any]:
    """Research attendee's professional background and experience."""
    research_data = {
        "name": name,
        "title": title,
        "company": company_name,
        "linkedin_url": linkedin_url,
        "background_info": [],
        "career_highlights": [],
        "expertise_areas": []
    }
    
    if not name:
        return research_data
    
    # Search for professional background
    background_query = f"{name} {company_name} background experience career"
    if title:
        background_query += f" {title}"
    
    try:
        background_results = await web_search(background_query, 3)
        research_data["background_info"] = background_results
        
        # Search for specific expertise and achievements
        expertise_query = f"{name} {company_name} expertise achievements awards"
        expertise_results = await web_search(expertise_query, 2)
        research_data["career_highlights"] = expertise_results
        
    except Exception:
        pass  # Continue even if research fails
    
    return research_data

async def create_hubspot_contact(attendee_data: Dict[str, Any]) -> Optional[str]:
    """Create a new contact in HubSpot and return the contact ID."""
    if not HUBSPOT_TOKEN:
        return None
    
    try:
        properties = {
            "firstname": attendee_data.get("name", "").split()[0] if attendee_data.get("name") else "",
            "lastname": " ".join(attendee_data.get("name", "").split()[1:]) if attendee_data.get("name") and len(attendee_data.get("name", "").split()) > 1 else "",
            "jobtitle": attendee_data.get("title", ""),
            "company": attendee_data.get("company", ""),
        }
        
        # Add email if available
        if attendee_data.get("email"):
            properties["email"] = attendee_data["email"]
        
        # Add LinkedIn URL if available
        if attendee_data.get("linkedin_url"):
            properties["linkedin_url"] = attendee_data["linkedin_url"]
        
        payload = {"properties": properties}
        
        response = await hubspot_post("/crm/v3/objects/contacts", payload)
        return response.get("id")
        
    except Exception:
        return None

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

    button.secondary{
      background: var(--cro-plat-300);
      color: var(--cro-soft-black-700);
      font-size: 0.9rem;
      padding: 0.5rem 1rem;
    }

    button.secondary:hover{
      background: var(--cro-plat-400);
    }

    button.remove{
      background: var(--cro-red-500);
      color: var(--cro-white);
      font-size: 0.8rem;
      padding: 0.4rem 0.8rem;
      margin-left: 0.5rem;
    }

    button.remove:hover{
      background: var(--cro-red-600);
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

    .attendees-section {
      background: var(--cro-blue-100);
      border: 1px solid var(--cro-blue-200);
      border-radius: 12px;
      padding: 1.5rem;
      margin: 1rem 0;
    }

    .attendee-item {
      background: var(--cro-white);
      border: 1px solid var(--cro-plat-300);
      border-radius: 8px;
      padding: 1rem;
      margin: 0.5rem 0;
      display: flex;
      flex-direction: column;
      gap: 1rem;
    }

    .attendee-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
    }

    .attendee-fields {
      display: grid;
      grid-template-columns: 2fr 2fr 2fr 1fr;
      gap: 1rem;
      flex: 1;
    }

    .attendee-status {
      font-size: 0.8rem;
      padding: 0.25rem 0.5rem;
      border-radius: 4px;
      margin-left: 1rem;
    }

    .status-unknown {
      background: var(--cro-yellow-100);
      color: var(--cro-yellow-700);
    }

    .status-found {
      background: var(--cro-green-100);
      color: var(--cro-green-700);
    }

    .status-new {
      background: var(--cro-blue-100);
      color: var(--cro-blue-700);
    }

    .status-researched {
      background: var(--cro-purple-400);
      color: var(--cro-white);
    }

    .attendee-actions {
      display: flex;
      gap: 0.5rem;
      align-items: center;
    }

    .hubspot-btn {
      background: var(--cro-green-600);
      color: var(--cro-white);
      font-size: 0.8rem;
      padding: 0.4rem 0.8rem;
    }

    .hubspot-btn:hover {
      background: var(--cro-green-700);
    }

    .research-phase {
      background: var(--cro-yellow-100);
      border: 1px solid var(--cro-yellow-400);
      border-radius: 12px;
      padding: 1.5rem;
      margin: 1rem 0;
    }

    .phase-complete {
      background: var(--cro-green-100);
      border-color: var(--cro-green-400);
    }

    .linkedin-snippet {
      background: var(--cro-blue-100);
      border: 1px solid var(--cro-blue-200);
      border-radius: 8px;
      padding: 1rem;
      margin-top: 0.5rem;
      font-size: 0.9rem;
      line-height: 1.4;
    }

    .linkedin-link {
      color: var(--cro-blue-700);
      text-decoration: none;
      font-weight: 600;
      display: inline-flex;
      align-items: center;
      gap: 0.5rem;
      margin-top: 0.5rem;
    }

    .linkedin-link:hover {
      color: var(--cro-blue-800);
      text-decoration: underline;
    }

    .hubspot-status {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      font-size: 0.9rem;
      margin-top: 0.5rem;
    }

    .hubspot-status.found {
      color: var(--cro-green-700);
    }

    .hubspot-status.not-found {
      color: var(--cro-purple-700);
    }

    .research-results {
      display: none;
      margin-top: 1rem;
      padding-top: 1rem;
      border-top: 1px solid var(--cro-plat-300);
    }

    .research-results.show {
      display: block;
    }

    .hubspot-options {
      background: var(--cro-green-100);
      border: 1px solid var(--cro-green-200);
      border-radius: 8px;
      padding: 1rem;
      margin: 1rem 0;
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
      
      .attendee-fields {
        grid-template-columns: 1fr;
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

    <div class="attendees-section">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem;">
        <label style="margin: 0; font-size: 1.1rem;">Meeting Attendees</label>
        <button type="button" class="secondary" onclick="addAttendee()">+ Add Attendee</button>
      </div>
      <div class="muted" style="margin-bottom: 1rem;">Add all meeting attendees for comprehensive research and LinkedIn discovery.</div>
      
      <div id="attendees-list">
        <!-- Attendees will be added here dynamically -->
      </div>
      
      <div class="hubspot-options">
        <label>
          <input type="checkbox" id="check-hubspot" checked> Check HubSpot for existing contacts during research
        </label>
        <div class="muted" style="margin-top: 0.5rem;">Individual "Add to HubSpot" buttons will appear for each attendee after research</div>
      </div>
    </div>

    <label for="meeting_context">Meeting Context & Objectives</label>
    <textarea id="meeting_context" placeholder="External BD meeting to explore partnership opportunities. Focus on digital transformation, data analytics, and consumer insights capabilities..."></textarea>

    <label for="prompt">Research Instructions</label>
    <textarea id="prompt">Create a strategic business development intelligence report using the research provided below.
Focus on identifying specific opportunities where CROmetrics can drive measurable business impact.
Base all analysis on the research context provided. Mark gaps as **Unknown** and prioritize additional research needs.
Position CROmetrics as the strategic partner who understands their business and can accelerate their transformation goals.</textarea>

    <div class="research-phase" id="research-phase">
      <h3 style="margin-top: 0; color: var(--cro-yellow-700);">Phase 1: Research Attendees</h3>
      <p class="muted">First, let's research each attendee to gather their LinkedIn profiles and professional background.</p>
      <div class="row">
        <button id="research-attendees">Research All Attendees</button>
        <div id="research-status" class="muted" style="align-self: center; margin-left: 1rem;"></div>
      </div>
    </div>

    <div class="research-phase phase-complete" id="intelligence-phase" style="display: none;">
      <h3 style="margin-top: 0; color: var(--cro-green-700);">Phase 2: Generate Intelligence Report</h3>
      <p class="muted">Now that we have researched all attendees, generate the comprehensive meeting intelligence report.</p>
      <div class="row">
        <button id="run">Generate Intelligence Report</button>
        <div id="status" class="muted" style="align-self: center; margin-left: 1rem;"></div>
      </div>
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
    let attendeeCounter = 0;

    function addAttendee(name = '', title = '', email = '') {
      attendeeCounter++;
      const attendeesList = document.getElementById('attendees-list');
      
      const attendeeDiv = document.createElement('div');
      attendeeDiv.className = 'attendee-item';
      attendeeDiv.id = `attendee-${attendeeCounter}`;
      
      attendeeDiv.innerHTML = `
        <div class="attendee-header">
          <div class="attendee-fields">
            <input type="text" placeholder="Full Name" value="${name}" data-field="name">
            <input type="text" placeholder="Title/Role" value="${title}" data-field="title">
            <input type="text" placeholder="Company" value="" data-field="company">
            <input type="email" placeholder="Email (optional)" value="${email}" data-field="email">
          </div>
          <div style="display: flex; align-items: center; gap: 1rem;">
            <div class="attendee-status status-unknown" id="status-${attendeeCounter}">Unknown</div>
            <div class="attendee-actions" id="actions-${attendeeCounter}" style="display: none;">
              <button type="button" class="secondary hubspot-btn" onclick="addToHubSpot(${attendeeCounter})" style="display: none;">Add to HubSpot</button>
            </div>
            <button type="button" class="remove" onclick="removeAttendee(${attendeeCounter})">Remove</button>
          </div>
        </div>
        <div class="research-results" id="research-${attendeeCounter}">
          <!-- Research results will be populated here -->
        </div>
      `;
      
      attendeesList.appendChild(attendeeDiv);
      
      if (attendeesList.children.length === 1) {
        // First attendee, don't allow removal
        attendeeDiv.querySelector('.remove').style.display = 'none';
      }
    }

    function removeAttendee(id) {
      const attendeeDiv = document.getElementById(`attendee-${id}`);
      if (attendeeDiv) {
        attendeeDiv.remove();
      }
      
      // If only one attendee left, hide its remove button
      const attendeesList = document.getElementById('attendees-list');
      if (attendeesList.children.length === 1) {
        attendeesList.querySelector('.remove').style.display = 'none';
      }
    }

    function getAttendees() {
      const attendees = [];
      const attendeeItems = document.querySelectorAll('.attendee-item');
      
      attendeeItems.forEach(item => {
        const name = item.querySelector('[data-field="name"]').value.trim();
        const title = item.querySelector('[data-field="title"]').value.trim();
        const company = item.querySelector('[data-field="company"]').value.trim();
        const email = item.querySelector('[data-field="email"]').value.trim();
        
        if (name) {
          attendees.push({
            name: name,
            title: title,
            company: company,
            email: email
          });
        }
      });
      
      return attendees;
    }

    let attendeeResearchData = [];

    function addToHubSpot(attendeeId) {
      const attendee = attendeeResearchData.find(a => a.ui_id === attendeeId);
      if (!attendee) return;

      const statusEl = document.getElementById(`status-${attendeeId}`);
      const hubspotBtn = document.querySelector(`#actions-${attendeeId} .hubspot-btn`);
      
      statusEl.textContent = 'Adding to HubSpot...';
      hubspotBtn.disabled = true;

      // Call API to add to HubSpot
      fetch('/api/bd/add-to-hubspot', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          attendee: {
            name: attendee.name,
            title: attendee.title,
            company: attendee.company,
            email: attendee.email,
            linkedin_url: attendee.linkedin_url
          }
        })
      })
      .then(response => response.json())
      .then(data => {
        if (data.success) {
          statusEl.textContent = 'Added to HubSpot';
          statusEl.className = 'attendee-status status-found';
          hubspotBtn.style.display = 'none';
          attendee.hubspot_contact = {id: data.contact_id, created: true};
        } else {
          statusEl.textContent = 'HubSpot Error';
          hubspotBtn.disabled = false;
        }
      })
      .catch(error => {
        statusEl.textContent = 'HubSpot Error';
        hubspotBtn.disabled = false;
      });
    }

    async function researchAttendees() {
      const attendees = getAttendees();
      if (attendees.length === 0) {
        alert('Please add at least one attendee');
        return;
      }

      const targetCompany = document.getElementById('company').value.trim();
      if (!targetCompany) {
        alert('Please enter the target company name');
        return;
      }

      document.getElementById('research-attendees').disabled = true;
      document.getElementById('research-status').textContent = 'Researching attendees...';
      
      attendeeResearchData = [];

      try {
        const response = await fetch('/api/bd/research-attendees', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            attendees: attendees,
            target_company: targetCompany,
            check_hubspot: document.getElementById('check-hubspot').checked
          })
        });

        const data = await response.json();
        
        if (response.ok) {
          attendeeResearchData = data.researched_attendees;
          
          // Update UI with research results
          attendeeResearchData.forEach((attendee, index) => {
            const attendeeId = index + 1; // Assuming sequential IDs
            attendee.ui_id = attendeeId;
            
            const statusEl = document.getElementById(`status-${attendeeId}`);
            const actionsEl = document.getElementById(`actions-${attendeeId}`);
            const hubspotBtn = actionsEl.querySelector('.hubspot-btn');
            const researchResultsEl = document.getElementById(`research-${attendeeId}`);
            
            // Auto-populate fields with discovered information
            const nameField = document.querySelector(`#attendee-${attendeeId} [data-field="name"]`);
            const titleField = document.querySelector(`#attendee-${attendeeId} [data-field="title"]`);
            const companyField = document.querySelector(`#attendee-${attendeeId} [data-field="company"]`);
            const emailField = document.querySelector(`#attendee-${attendeeId} [data-field="email"]`);
            
            // Update fields if we have better information
            if (attendee.company && !companyField.value) {
              companyField.value = attendee.company;
            }
            
            // Update status
            if (attendee.linkedin_url) {
              statusEl.textContent = `✓ LinkedIn Found`;
              statusEl.className = 'attendee-status status-researched';
            } else {
              statusEl.textContent = 'No LinkedIn Found';
              statusEl.className = 'attendee-status status-unknown';
            }

            // Build research results HTML
            let researchHtml = '';
            
            // HubSpot Status
            if (attendee.hubspot_contact) {
              researchHtml += `
                <div class="hubspot-status found">
                  ✅ <strong>Found in HubSpot</strong> (Contact ID: ${attendee.hubspot_contact.id || 'N/A'})
                </div>
              `;
              statusEl.textContent += ', In HubSpot';
              statusEl.className = 'attendee-status status-found';
            } else {
              researchHtml += `
                <div class="hubspot-status not-found">
                  ℹ️ <strong>Not in HubSpot</strong> - Add button will appear below
                </div>
              `;
              // Show HubSpot button if not in HubSpot (email not required)
              hubspotBtn.style.display = 'inline-block';
              actionsEl.style.display = 'flex';
            }
            
            // LinkedIn Information
            if (attendee.linkedin_url) {
              researchHtml += `
                <a href="${attendee.linkedin_url}" target="_blank" class="linkedin-link">
                  🔗 View LinkedIn Profile
                </a>
              `;
              
              if (attendee.linkedin_snippet) {
                researchHtml += `
                  <div class="linkedin-snippet">
                    <strong>${attendee.linkedin_title || 'LinkedIn Profile'}</strong><br>
                    ${attendee.linkedin_snippet}
                  </div>
                `;
              }
            } else {
              researchHtml += `
                <div class="linkedin-snippet" style="background: var(--cro-yellow-100); border-color: var(--cro-yellow-400);">
                  ⚠️ LinkedIn profile not found. You may want to search manually or verify the name/company.
                </div>
              `;
            }
            
            researchResultsEl.innerHTML = researchHtml;
            researchResultsEl.classList.add('show');
          });

          document.getElementById('research-status').textContent = `Research complete! Found ${data.linkedin_found} LinkedIn profiles.`;
          
          // Show Phase 2
          document.getElementById('research-phase').style.display = 'none';
          document.getElementById('intelligence-phase').style.display = 'block';
          
        } else {
          throw new Error(data.detail || 'Research failed');
        }
        
      } catch (error) {
        document.getElementById('research-status').textContent = 'Research failed: ' + error.message;
      } finally {
        document.getElementById('research-attendees').disabled = false;
      }
    }

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
      statusEl.textContent = 'Generating intelligence report...';
      progressEl.style.display = 'block';
      progressSteps.innerHTML = '';
      document.getElementById('run').disabled = true;
      
      try{
        if (attendeeResearchData.length === 0) {
          throw new Error('Please research attendees first');
        }

        const body = {
          company_name: document.getElementById('company').value,
          industry: document.getElementById('industry').value,
          meeting_context: document.getElementById('meeting_context').value,
          effort: document.getElementById('effort').value,
          prompt: document.getElementById('prompt').value,
          researched_attendees: attendeeResearchData
        };
        
        updateProgress('Generating intelligence report with researched attendee data...');
        
        const r = await fetch('/api/bd/generate', {
          method:'POST', 
          headers:{'Content-Type':'application/json'}, 
          body: JSON.stringify(body)
        });
        
        const data = await r.json();
        if(!r.ok){ throw new Error(data.detail || JSON.stringify(data)); }
        
        updateProgress('Intelligence report generated successfully!');
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

    // Initialize with one attendee
    addAttendee();
    
    document.getElementById('research-attendees').addEventListener('click', researchAttendees);
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

    # Handle both old format and new researched attendees format
    researched_attendees = payload.get("researched_attendees", [])
    if researched_attendees:
        # New workflow: use pre-researched attendees
        enriched_attendees = researched_attendees
    else:
        # Legacy workflow: research attendees inline (backwards compatibility)
        attendees_data = payload.get("attendees", [])
        if not attendees_data:
            # Fallback to old format for backwards compatibility
            executive_name = (payload.get("executive_name") or "").strip()
            executive_title = (payload.get("executive_title") or "").strip()
            if executive_name:
                attendees_data = [{"name": executive_name, "title": executive_title, "email": ""}]

        if not attendees_data:
            raise HTTPException(status_code=400, detail="At least one attendee is required")
        
        # Legacy inline research (for backwards compatibility)
        enriched_attendees = []
        hubspot_contacts = []
        
        # Check HubSpot for existing contacts if requested
        check_hubspot = payload.get("check_hubspot", True)
        if check_hubspot and HUBSPOT_TOKEN:
            attendee_emails = [a.get("email") for a in attendees_data if a.get("email")]
            if attendee_emails:
                try:
                    hubspot_contacts = await fetch_contacts_by_email(attendee_emails)
                except Exception:
                    hubspot_contacts = []
        
        for attendee in attendees_data:
            name = attendee.get("name", "").strip()
            title = attendee.get("title", "").strip()
            email = attendee.get("email", "").strip()
            
            if not name:
                continue
                
            enriched_attendee = {
                "name": name,
                "title": title,
                "email": email,
                "company": company_name,
                "linkedin_url": None,
                "linkedin_snippet": None,
                "linkedin_title": None,
                "hubspot_contact": None,
                "background_research": None
            }
            
            # Check if this attendee exists in HubSpot
            if email and hubspot_contacts:
                for contact in hubspot_contacts:
                    if contact.get("email", "").lower() == email.lower():
                        enriched_attendee["hubspot_contact"] = contact
                        enriched_attendee["linkedin_url"] = contact.get("linkedin_url")
                        break
            
            # LinkedIn discovery if not already found in HubSpot
            if not enriched_attendee["linkedin_url"]:
                linkedin_data = await research_attendee_linkedin(name, company_name, title)
                enriched_attendee["linkedin_url"] = linkedin_data.get("url")
                enriched_attendee["linkedin_snippet"] = linkedin_data.get("snippet")
                enriched_attendee["linkedin_title"] = linkedin_data.get("title")
            
            # Background research
            background_data = await research_attendee_background(
                name, company_name, title, enriched_attendee["linkedin_url"] or ""
            )
            enriched_attendee["background_research"] = background_data
            
            enriched_attendees.append(enriched_attendee)

    industry = (payload.get("industry") or "").strip()
    meeting_context = (payload.get("meeting_context") or "").strip()
    effort = (payload.get("effort") or "high").lower()
    prompt = (payload.get("prompt") or BD_DEFAULT_PROMPT).strip()

    # 1) Company research
    # Use the first attendee's name for company research
    primary_attendee = enriched_attendees[0] if enriched_attendees else {"name": ""}
    research_data = await research_company(company_name, primary_attendee.get("name", ""))
    
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
    
    # Attendee profiles
    if enriched_attendees:
        research_sections.append("## Meeting Attendee Profiles")
        for attendee in enriched_attendees:
            research_sections.append(f"### {attendee['name']}")
            research_sections.append(f"**Title:** {attendee['title'] or 'Not specified'}")
            research_sections.append(f"**Email:** {attendee['email'] or 'Not provided'}")
            if attendee['linkedin_url']:
                research_sections.append(f"**LinkedIn:** {attendee['linkedin_url']}")
            
            if attendee['hubspot_contact']:
                contact = attendee['hubspot_contact']
                if contact.get('created'):
                    research_sections.append("**HubSpot Status:** New contact created")
                else:
                    research_sections.append(f"**HubSpot Status:** Existing contact found (ID: {contact.get('id', 'N/A')})")
            
            # Include background research
            if attendee['background_research']:
                bg_research = attendee['background_research']
                if bg_research.get('background_info'):
                    research_sections.append("**Professional Background:**")
                    for item in bg_research['background_info'][:2]:  # Limit to top 2 results
                        research_sections.append(f"- {item.get('title', 'N/A')}")
                        research_sections.append(f"  {item.get('snippet', 'No snippet available')}")
                
                if bg_research.get('career_highlights'):
                    research_sections.append("**Career Highlights:**")
                    for item in bg_research['career_highlights'][:1]:  # Limit to top result
                        research_sections.append(f"- {item.get('snippet', 'No information available')}")
            
            research_sections.append("")  # Add spacing between attendees
    
    # Competitive landscape
    if competitive_data:
        research_sections.append("## Competitive Landscape Analysis")
        for item in competitive_data:
            research_sections.append(f"**{item.get('title', 'N/A')}**")
            research_sections.append(f"Source: {item.get('link', 'N/A')}")
            research_sections.append(f"{item.get('snippet', 'No snippet available')}\n")

    research_context = "\n".join(research_sections) if research_sections else "No research data available."
    
    # 4) Compose full context
    attendee_summary = ", ".join([f"{a['name']} ({a['title'] or 'Title TBD'})" for a in enriched_attendees])
    composed_context = (
        f"TARGET COMPANY: {company_name}\n"
        f"MEETING ATTENDEES: {attendee_summary}\n"
        f"INDUSTRY: {industry or 'Not specified'}\n"
        f"MEETING CONTEXT: {meeting_context or 'Not provided'}\n\n"
        f"RESEARCH INTELLIGENCE:\n{research_context}"
    )

    # Log intelligence report generation
    log_usage("intelligence_report", {
        "company_name": company_name,
        "industry": industry,
        "attendee_count": len(enriched_attendees),
        "effort": effort,
        "prompt_length": len(prompt),
        "context_length": len(composed_context)
    }, req)

    # 5) Generate BD intelligence report
    report = await asyncio.wait_for(ask_o3_bd(prompt, composed_context, effort=effort), timeout=300.0)

    return JSONResponse({
        "report_markdown": report,
        "meta": {
            "company_name": company_name,
            "attendees_researched": len(enriched_attendees),
            "linkedin_urls_found": sum(1 for a in enriched_attendees if a["linkedin_url"]),
            "hubspot_contacts_found": sum(1 for a in enriched_attendees if a["hubspot_contact"] and not a["hubspot_contact"].get("created")),
            "hubspot_contacts_created": sum(1 for a in enriched_attendees if a["hubspot_contact"] and a["hubspot_contact"].get("created")),
            "research_sections": len(research_sections),
            "effort": effort,
        }
    })

@app.post("/api/bd/research-attendees")
async def api_bd_research_attendees(req: Request) -> JSONResponse:
    """Research attendees phase - separate from intelligence report generation."""
    payload = await req.json()
    
    attendees_data = payload.get("attendees", [])
    if not attendees_data:
        raise HTTPException(status_code=400, detail="At least one attendee is required")
    
    target_company = payload.get("target_company", "").strip()
    check_hubspot = payload.get("check_hubspot", True)
    
    # Log usage for analytics
    log_usage("attendee_research", {
        "target_company": target_company,
        "attendee_count": len(attendees_data),
        "attendees": [{"name": a.get("name"), "title": a.get("title"), "company": a.get("company")} for a in attendees_data],
        "check_hubspot": check_hubspot
    }, req)
    
    # Research each attendee
    enriched_attendees = []
    hubspot_contacts = []
    
    # Check HubSpot for existing contacts if requested
    if check_hubspot and HUBSPOT_TOKEN:
        attendee_emails = [a.get("email") for a in attendees_data if a.get("email")]
        if attendee_emails:
            try:
                hubspot_contacts = await fetch_contacts_by_email(attendee_emails)
            except Exception:
                hubspot_contacts = []
    
    for attendee in attendees_data:
        name = attendee.get("name", "").strip()
        title = attendee.get("title", "").strip()
        company = attendee.get("company", "").strip() or target_company
        email = attendee.get("email", "").strip()
        
        if not name:
            continue
            
        enriched_attendee = {
            "name": name,
            "title": title,
            "company": company,
            "email": email,
            "linkedin_url": None,
            "linkedin_snippet": None,
            "linkedin_title": None,
            "hubspot_contact": None,
            "background_research": None
        }
        
        # Check if this attendee exists in HubSpot
        if email and hubspot_contacts:
            for contact in hubspot_contacts:
                if contact.get("email", "").lower() == email.lower():
                    enriched_attendee["hubspot_contact"] = contact
                    enriched_attendee["linkedin_url"] = contact.get("linkedin_url")
                    break
        
        # LinkedIn discovery if not already found in HubSpot
        if not enriched_attendee["linkedin_url"]:
            linkedin_data = await research_attendee_linkedin(name, company, title)
            enriched_attendee["linkedin_url"] = linkedin_data.get("url")
            enriched_attendee["linkedin_snippet"] = linkedin_data.get("snippet")
            enriched_attendee["linkedin_title"] = linkedin_data.get("title")
        
        # Background research
        background_data = await research_attendee_background(
            name, company, title, enriched_attendee["linkedin_url"] or ""
        )
        enriched_attendee["background_research"] = background_data
        
        enriched_attendees.append(enriched_attendee)
    
    return JSONResponse({
        "researched_attendees": enriched_attendees,
        "linkedin_found": sum(1 for a in enriched_attendees if a["linkedin_url"]),
        "hubspot_found": sum(1 for a in enriched_attendees if a["hubspot_contact"]),
        "total_researched": len(enriched_attendees)
    })

@app.post("/api/bd/add-to-hubspot")
async def api_bd_add_to_hubspot(req: Request) -> JSONResponse:
    """Add a single attendee to HubSpot."""
    payload = await req.json()
    
    attendee_data = payload.get("attendee", {})
    if not attendee_data.get("name"):
        raise HTTPException(status_code=400, detail="Attendee name is required")
    
    # Log HubSpot contact creation
    log_usage("hubspot_contact_add", {
        "name": attendee_data.get("name"),
        "title": attendee_data.get("title"),
        "company": attendee_data.get("company"),
        "has_email": bool(attendee_data.get("email")),
        "has_linkedin": bool(attendee_data.get("linkedin_url"))
    }, req)
    
    contact_id = await create_hubspot_contact(attendee_data)
    
    if contact_id:
        return JSONResponse({
            "success": True,
            "contact_id": contact_id,
            "message": "Contact created successfully"
        })
    else:
        return JSONResponse({
            "success": False,
            "message": "Failed to create HubSpot contact"
        }, status_code=400)

@app.get("/api/usage-logs")
async def api_usage_logs(req: Request) -> JSONResponse:
    """View usage logs for analysis (last 100 entries)."""
    try:
        if not os.path.exists(usage_log_file):
            return JSONResponse({"logs": [], "message": "No usage logs found"})
        
        # Read the last 100 lines of the log file
        with open(usage_log_file, 'r') as f:
            lines = f.readlines()
        
        # Get last 100 lines and parse as JSON
        recent_lines = lines[-100:] if len(lines) > 100 else lines
        logs = []
        
        for line in recent_lines:
            try:
                # Parse the log entry (format: timestamp | json_data)
                if ' | ' in line:
                    timestamp_str, json_str = line.split(' | ', 1)
                    log_data = json.loads(json_str.strip())
                    logs.append(log_data)
            except (json.JSONDecodeError, ValueError):
                continue
        
        return JSONResponse({
            "logs": logs,
            "total_entries": len(logs),
            "log_file_path": usage_log_file
        })
        
    except Exception as e:
        return JSONResponse({
            "error": str(e),
            "message": "Failed to read usage logs"
        }, status_code=500)

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
