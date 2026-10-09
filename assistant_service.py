import os
import re
import json
from datetime import datetime

import models
import ai_service

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

STOPWORDS = {
    'a', 'an', 'the', 'in', 'on', 'at', 'by', 'for', 'with', 'about', 'against',
    'between', 'into', 'through', 'during', 'before', 'after', 'above', 'below',
    'to', 'from', 'up', 'down', 'of', 'off', 'over', 'under', 'again', 'further',
    'then', 'once', 'here', 'there', 'when', 'where', 'why', 'how', 'all', 'any',
    'both', 'each', 'few', 'more', 'most', 'other', 'some', 'such', 'no', 'nor',
    'not', 'only', 'own', 'same', 'so', 'than', 'too', 'very', 's', 't', 'can',
    'will', 'just', 'don', 'should', 'now', 'i', 'my', 'me', 'it', 'its', 'was',
    'is', 'am', 'are', 'were', 'be', 'been', 'being', 'have', 'has', 'had', 'do',
    'does', 'did', 'want', 'need', 'help', 'please', 'tell', 'show', 'search',
    'find', 'look', 'looking', 'what', 'which', 'who', 'whom', 'this', 'that'
}

def extract_search_terms(text):
    """Extracts meaningful alphanumeric search keywords from user prompt."""
    if not text:
        return []
    words = re.findall(r'[a-zA-Z0-9]+', text.lower())
    terms = [w for w in words if len(w) > 2 and w not in STOPWORDS]
    return terms

def find_relevant_listings_for_prompt(user_text, limit=4):
    """
    Searches the SQLite database for real listings matching user search terms.
    Returns sanitized public records only (no secret_details or private phones).
    """
    terms = extract_search_terms(user_text)
    if not terms:
        # Fallback to recent open listings if user simply says "browse" or "listings"
        if any(w in user_text.lower() for w in ['listing', 'item', 'recent', 'everything', 'all', 'what has been']):
            raw_items = models.get_items(limit=limit)
        else:
            return []
    else:
        query_string = " ".join(terms[:3])
        raw_items = models.get_items(query=query_string, limit=limit)
        if not raw_items and len(terms) > 1:
            # Try single top keyword
            raw_items = models.get_items(query=terms[0], limit=limit)

    sanitized = []
    for it in raw_items:
        sanitized.append({
            'id': it['id'],
            'type': it['type'],
            'title': it['title'],
            'category': it['category'],
            'location': it['location'],
            'incident_date': it['incident_date'],
            'status': it['status'],
            'description': it['description'][:140] + ('...' if len(it['description']) > 140 else '')
        })
    return sanitized

def deterministic_assistant_reply(user_message, relevant_items=None, page_context=None):
    """
    High-fidelity deterministic rule-based assistant fallback.
    Used when Gemini API is unconfigured or encounters connection/rate-limit issues.
    """
    msg = user_message.lower().strip()
    actions = []

    # 0. Personal Activity & History queries
    if any(k in msg for k in ['my history', 'my activity', 'items have i', 'have i reported', 'my reported', 'my reports', 'my claims', 'claims have i', 'happened to my', 'what did i lose', 'what did i find', 'my lost item', 'my found item']):
        auth = page_context.get('auth') if (page_context and isinstance(page_context, dict)) else None
        if not auth or not auth.get('logged_in'):
            reply = (
                "Your personal lost-and-found history is protected. "
                "Please sign in with your account to view your reported items, submitted claims, and potential matches."
            )
            actions.append({'label': "Sign In to View History", 'url': "/login"})
            actions.append({'label': "Register Account", 'url': "/register"})
            return {'reply': reply, 'actions': actions}

        user_id = auth.get('user_id')
        user_email = auth.get('user_email')
        user_name = auth.get('user_name', 'there')
        user_items = models.get_user_items(user_id, email=user_email)
        user_claims = models.get_user_claims(user_id, email=user_email)

        if not user_items and not user_claims:
            reply = (
                f"Hello {user_name}. You currently have no lost or found reports or ownership claims on file. "
                "You can submit a new report or explore active community listings."
            )
            actions.append({'label': "Open My Activity", 'url': "/activity"})
            actions.append({'label': "Report an Item", 'url': "/report"})
            actions.append({'label': "Browse Listings", 'url': "/items"})
            return {'reply': reply, 'actions': actions}

        summary_lines = [f"Here is your personal activity summary, {user_name}:"]
        if user_items:
            summary_lines.append(f"\nReported Belongings ({len(user_items)}):")
            for it in user_items[:3]:
                status_label = models.STATUS_CHOICES.get(it['status'], it['status'].title())
                summary_lines.append(f"- #{it['id']} [{it['type'].upper()}]: {it['title']} ({status_label})")
                actions.append({'label': f"View #{it['id']} - {it['title']}", 'url': f"/items/{it['id']}"})
        if user_claims:
            summary_lines.append(f"\nSubmitted Claims ({len(user_claims)}):")
            for cl in user_claims[:2]:
                summary_lines.append(f"- Claim for '{cl['item_title']}' — Status: {cl['status'].replace('_', ' ').title()}")

        summary_lines.append("\nYou can view your complete personal history, filter reports, and inspect potential matches in My Activity.")
        actions.append({'label': "Open My Activity Dashboard", 'url': "/activity"})
        return {'reply': "\n".join(summary_lines), 'actions': actions}

    # 1. Search / Item lookup queries
    if relevant_items:
        item_bullets = []
        for it in relevant_items:
            status_text = models.STATUS_CHOICES.get(it['status'], it['status'].title())
            item_bullets.append(
                f"- #{it['id']} ({it['type'].upper()}): {it['title']} at {it['location']} ({it['incident_date']}) - Status: {status_text}"
            )
            actions.append({
                'label': f"View #{it['id']} - {it['title']}",
                'url': f"/items/{it['id']}"
            })

        reply = (
            f"I found {len(relevant_items)} relevant listing(s) in the FindBack community database:\n\n"
            + "\n".join(item_bullets)
            + "\n\nYou can click any listing below to review complete public details, run Smart Match scans, or submit a claim if you are the owner."
        )
        actions.append({'label': "Browse All Listings", 'url': "/items"})
        return {'reply': reply, 'actions': actions}

    # 2. Reporting Lost or Found Items
    if any(k in msg for k in ['report', 'lost my', 'i lost', 'misplaced', 'found a', 'i found', 'how to report', 'post']):
        if 'found' in msg and 'lost' not in msg:
            reply = (
                "To report an unattended item you discovered:\n\n"
                "1. Go to the Report form and select 'I Found an Item'.\n"
                "2. Provide an accurate headline, category, and discovery location.\n"
                "3. Optionally upload a clear photograph (under 5 MB, avoiding sensitive personal details).\n"
                "4. Add private verification details in the confidential section (e.g. hidden markings or serial numbers). These are kept secret from the public and used only by reviewers to evaluate claims.\n"
                "5. Submit the report so the Smart Match Agent can immediately look for owners."
            )
            actions.append({'label': "Report Found Item", 'url': "/report?type=found"})
        else:
            reply = (
                "To report a misplaced personal belonging:\n\n"
                "1. Go to the Report form and choose 'I Lost an Item'.\n"
                "2. Enter a specific title, category, and the approximate location where it was lost.\n"
                "3. Describe distinctive public attributes such as color, brand, and stickers.\n"
                "4. Enter any confidential verification details (e.g. lock screen wallpaper or engraved initials). These remain strictly hidden from the public.\n"
                "5. Once published, our Smart Match Agent scans all found items and notifies you if a high match is discovered."
            )
            actions.append({'label': "Report Lost Item", 'url': "/report?type=lost"})

        actions.append({'label': "Browse Current Listings", 'url': "/items"})
        return {'reply': reply, 'actions': actions}

    # 3. Claiming Items
    if any(k in msg for k in ['claim', 'how do i claim', 'verify ownership', 'proof', 'ownership']):
        reply = (
            "Ownership claims on FindBack follow a human-verified recovery protocol:\n\n"
            "1. Locate the found item listing in our directory.\n"
            "2. Click 'Claim Item' on the listing page.\n"
            "3. Submit unpublicized proof of ownership - describe secret markers, contents, or serial numbers that only the true owner would know.\n"
            "4. Our Claim Verification Agent generates tailored verification questions to assist staff.\n"
            "5. An authorized staff member conducts physical verification and approves or denies the claim. AI never autonomously approves ownership."
        )
        actions.append({'label': "Browse Found Listings", 'url': "/items?type=found"})
        actions.append({'label': "Smart Match Hub", 'url': "/matches"})
        return {'reply': reply, 'actions': actions}

    # 4. Smart Match & Algorithm Explanation
    if any(k in msg for k in ['smart match', 'match', 'score', 'algorithm', 'why does it match', 'signals', 'counterpart']):
        reply = (
            "The FindBack Smart Match Agent analyzes candidate pairings using multi-signal correlation:\n\n"
            "1. Description & Semantic Alignment (35%): Evaluates brands, models, accessories, and unique characteristics.\n"
            "2. Category Compatibility (30%): Checks category alignment between reports.\n"
            "3. Location Proximity (20%): Compares campus zones, buildings, and specific rooms.\n"
            "4. Timeline Logic (15%): Validates that the discovery date follows the reported loss date within plausible timeframes.\n\n"
            "Important: A potential match score is an exploratory indicator, not proof of ownership. All ownership claims must undergo human staff review."
        )
        actions.append({'label': "Explore Smart Match Hub", 'url': "/matches"})
        actions.append({'label': "Browse Listings", 'url': "/items"})
        return {'reply': reply, 'actions': actions}

    # 5. Status Meanings
    if any(k in msg for k in ['status', 'open', 'claim_pending', 'resolved', 'closed', 'what does status mean']):
        reply = (
            "FindBack uses four clear listing statuses:\n\n"
            "- Active / Unclaimed (open): The item report is active and awaiting counterpart discovery or claims.\n"
            "- Claim Under Review (claim_pending): An ownership claim has been submitted and is currently being audited by staff.\n"
            "- Resolved & Returned (resolved): A verified claim was approved by staff and the item has been returned to its rightful owner.\n"
            "- Closed (closed): The report is no longer active."
        )
        actions.append({'label': "Browse Listings", 'url': "/items"})
        return {'reply': reply, 'actions': actions}

    # 6. Default general guidance
    reply = (
        "FindBack Web is a community lost-and-found platform developed by SEVONA LABS at SNS College of Technology. "
        "It connects people who have lost belongings with community members and campus security who have found them.\n\n"
        "I can help you:\n"
        "- Search active lost and found listings by keyword, category, or location.\n"
        "- Guide you through reporting lost or found property.\n"
        "- Explain Smart Match correlation scores and signals.\n"
        "- Guide you through submitting ownership claims with human review.\n\n"
        "How can I assist you today?"
    )
    actions.append({'label': "Report an Item", 'url': "/report"})
    actions.append({'label': "Browse Listings", 'url': "/items"})
    actions.append({'label': "Smart Match Hub", 'url': "/matches"})
    return {'reply': reply, 'actions': actions}

def process_assistant_message(user_message, conversation_history=None, page_context=None):
    """
    Processes incoming assistant message using Gemini API grounded in SQLite database listings.
    Gracefully falls back to deterministic rule-based assistant on missing key, rate limit, or network error.
    """
    if not user_message or not user_message.strip():
        return {
            'reply': "Please enter a question or specify an item you are looking for.",
            'actions': []
        }

    clean_message = user_message.strip()[:1000]

    # Retrieve real listings from SQLite
    relevant_items = find_relevant_listings_for_prompt(clean_message, limit=4)

    # If page_context has an item_id, fetch that item as well
    context_item = None
    if page_context and isinstance(page_context, dict) and page_context.get('item_id'):
        try:
            cid = int(page_context['item_id'])
            item_row = models.get_item_by_id(cid)
            if item_row:
                context_item = {
                    'id': item_row['id'],
                    'type': item_row['type'],
                    'title': item_row['title'],
                    'category': item_row['category'],
                    'location': item_row['location'],
                    'incident_date': item_row['incident_date'],
                    'status': item_row['status'],
                    'description': item_row['description'][:200]
                }
        except Exception:
            pass

    client = ai_service.get_gemini_client()
    if not client:
        return deterministic_assistant_reply(clean_message, relevant_items, page_context)

    # Format database context for Gemini
    db_context_text = "No specific matching listings found in database for these search terms."
    if relevant_items:
        lines = []
        for it in relevant_items:
            lines.append(
                f"- Listing #{it['id']} [{it['type'].upper()}]: '{it['title']}' | Category: {it['category']} | Location: {it['location']} | Date: {it['incident_date']} | Status: {it['status']} | Description: {it['description']}"
            )
        db_context_text = "\n".join(lines)

    context_item_text = "None"
    if context_item:
        context_item_text = (
            f"Listing #{context_item['id']} [{context_item['type'].upper()}]: '{context_item['title']}' at {context_item['location']} ({context_item['incident_date']})"
        )

    # Personal user records context (isolated strictly per authenticated session)
    auth = page_context.get('auth') if (page_context and isinstance(page_context, dict)) else None
    user_records_text = "User is NOT signed in. Personal history is restricted. Explain that sign-in is required and link to /login if asked about personal records."
    if auth and auth.get('logged_in'):
        u_id = auth.get('user_id')
        u_email = auth.get('user_email')
        u_name = auth.get('user_name', 'User')
        u_items = models.get_user_items(u_id, email=u_email)
        u_claims = models.get_user_claims(u_id, email=u_email)
        u_lines = [f"Authenticated User: {u_name} ({u_email})"]
        if u_items:
            u_lines.append(f"User's Reported Items ({len(u_items)}):")
            for it in u_items[:5]:
                u_lines.append(f"- #{it['id']} [{it['type'].upper()}]: '{it['title']}' (Status: {it['status']}) at {it['location']}")
        else:
            u_lines.append("User has reported 0 items.")
        if u_claims:
            u_lines.append(f"User's Submitted Claims ({len(u_claims)}):")
            for cl in u_claims[:3]:
                u_lines.append(f"- Claim for item #{cl['item_id']} '{cl['item_title']}' (Status: {cl['status']})")
        else:
            u_lines.append("User has submitted 0 claims.")
        user_records_text = "\n".join(u_lines)

    # Format conversation history
    history_lines = []
    if conversation_history and isinstance(conversation_history, list):
        for turn in conversation_history[-6:]:
            role = "User" if turn.get('role') == 'user' else "Assistant"
            history_lines.append(f"{role}: {turn.get('content', '')}")
    history_text = "\n".join(history_lines) if history_lines else "None"

    prompt = f"""You are FindBack Assistant, the specialized AI guide for FindBack Web (Community Lost & Found Platform developed by SEVONA LABS, SNS College of Technology).

YOUR MISSION:
- Help users search and navigate actual community listings.
- Explain how to report lost or found items.
- Explain the Smart Match system (multi-signal correlation: category, description keywords, location proximity, date logic).
- Guide users through the ownership claim workflow (human review is strictly mandatory; AI never approves claims autonomously).
- Clarify claim and report statuses (open, claim_pending, resolved, closed).
- When user asks about their personal history, reports, or claims: use [AUTHENTICATED USER PERSONAL RECORDS] below to summarize their items and link to '/activity'. If user is NOT signed in, politely state that logging in is required and link to '/login'.

CRITICAL GROUNDING RULES:
1. ZERO FABRICATIONS: You must NEVER invent items, listing IDs, dates, locations, or statuses. Use ONLY the real database records provided below as ground truth for listings.
2. If real listings exist in the provided database section, mention their actual titles and IDs. If no listings match, say so honestly and advise refining search terms or reporting the item.
3. PRIVACY: Never reveal confidential verification markers, passwords, or private phone numbers.
4. OWNERSHIP: Never state that ownership is confirmed or established. Potential matches are exploratory correlations, not proof of ownership.
5. NO ACTIONS CLAIM: Never claim to have submitted a form, sent an email, or modified database state yourself. Direct the user to the real platform pages.
6. TONE & STYLE: Calm, professional, concise, editorial, and helpful. Do NOT use emojis.

[AUTHENTICATED USER PERSONAL RECORDS]
{user_records_text}

[REAL DATABASE LISTINGS RETRIEVED FOR USER'S QUERY]
{db_context_text}

[CURRENT PAGE ITEM CONTEXT]
{context_item_text}

[RECENT CONVERSATION HISTORY]
{history_text}

[CURRENT USER MESSAGE]
{clean_message}

You MUST respond strictly with valid JSON conforming to this schema:
{{
  "reply": "Your concise, direct response text explaining what was found or giving guidance. Use line breaks for readability. Do NOT include HTML tags.",
  "actions": [
    {{
      "label": "Action button text (e.g., View #2 — Dark Blue Apple Smartphone, or Report Lost Item)",
      "url": "Valid relative URL (e.g., /items/2, /report?type=lost, /report?type=found, /items, /matches)"
    }}
  ]
}}
Do NOT output markdown backticks or any outside text.
"""

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )
        text = response.text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?", "", text)
            text = re.sub(r"```$", "", text).strip()

        data = json.loads(text)
        reply = str(data.get('reply', '')).strip()
        actions = []

        raw_actions = data.get('actions', [])
        if isinstance(raw_actions, list):
            for act in raw_actions:
                if isinstance(act, dict) and act.get('label') and act.get('url'):
                    url = str(act['url']).strip()
                    # Security check: Ensure actions only point to safe relative application routes
                    if url.startswith(('/', '/items', '/report', '/matches', '/reviewer')):
                        actions.append({
                            'label': str(act['label']).strip(),
                            'url': url
                        })

        # If model returned no actions but we have real items, ensure we provide direct links
        if not actions and relevant_items:
            for it in relevant_items[:3]:
                actions.append({
                    'label': f"View #{it['id']} — {it['title']}",
                    'url': f"/items/{it['id']}"
                })

        return {
            'reply': reply or "I've reviewed your request. How else can I assist with your lost or found items?",
            'actions': actions
        }

    except Exception as e:
        print(f"[Assistant Gemini Error] {e}. Falling back to deterministic assistant.")
        return deterministic_assistant_reply(clean_message, relevant_items, page_context)

# Alias for backward compatibility
chat_with_assistant = process_assistant_message

