import os
import re
import json
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

def get_gemini_client():
    """Returns Gemini client if API key is present, otherwise None."""
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        from google import genai
        return genai.Client(api_key=api_key)
    except Exception as e:
        print(f"[AI Service Warning] Failed to initialize Gemini client: {e}")
        return None

# =========================================================================
# DETERMINISTIC SMART MATCH FALLBACK
# =========================================================================

STOPWORDS = {
    'a', 'an', 'the', 'in', 'on', 'at', 'by', 'for', 'with', 'about', 'against',
    'between', 'into', 'through', 'during', 'before', 'after', 'above', 'below',
    'to', 'from', 'up', 'down', 'of', 'off', 'over', 'under', 'again', 'further',
    'then', 'once', 'here', 'there', 'when', 'where', 'why', 'how', 'all', 'any',
    'both', 'each', 'few', 'more', 'most', 'other', 'some', 'such', 'no', 'nor',
    'not', 'only', 'own', 'same', 'so', 'than', 'too', 'very', 's', 't', 'can',
    'will', 'just', 'don', 'should', 'now', 'i', 'my', 'me', 'it', 'its', 'was',
    'is', 'left', 'found', 'lost', 'near', 'has', 'have', 'and', 'or', 'item'
}

def extract_keywords(text):
    """Extract meaningful lowercased alphanumeric keywords from text."""
    if not text:
        return set()
    words = re.findall(r'[a-zA-Z0-9]+', text.lower())
    return {w for w in words if len(w) > 2 and w not in STOPWORDS}

def deterministic_match_items(lost_item, found_item):
    """
    Ranks two items deterministically based on:
    - Category match (30%)
    - Location keyword overlap (20%)
    - Date proximity (15%)
    - Title & Description semantic keyword overlap (35%)
    """
    score = 0
    signals = []

    # 1. Category Signal (30 pts)
    cat_match = (lost_item['category'].lower() == found_item['category'].lower())
    if cat_match:
        score += 30
        signals.append(f"Category Match: {lost_item['category']}")
    else:
        signals.append("Category Discrepancy")

    # 2. Location Signal (20 pts)
    lost_loc_words = extract_keywords(lost_item['location'])
    found_loc_words = extract_keywords(found_item['location'])
    loc_overlap = lost_loc_words.intersection(found_loc_words)
    if loc_overlap:
        loc_score = min(20, 10 + len(loc_overlap) * 5)
        score += loc_score
        signals.append(f"Location Proximity: '{', '.join(loc_overlap)}'")
    elif any(term in lost_item['location'].lower() for term in ['library', 'union', 'gym', 'science', 'park', 'plaza', 'lot']) and \
         any(term in found_item['location'].lower() for term in ['library', 'union', 'gym', 'science', 'park', 'plaza', 'lot']):
        # Common zone match
        score += 10
        signals.append("Nearby Zone Match")

    # 3. Date Signal (15 pts)
    try:
        lost_date = datetime.strptime(lost_item['incident_date'], '%Y-%m-%d')
        found_date = datetime.strptime(found_item['incident_date'], '%Y-%m-%d')
        days_diff = (found_date - lost_date).days
        if 0 <= days_diff <= 7:
            score += 15
            signals.append(f"Timeline Match: Found {days_diff} day(s) after loss")
        elif -2 <= days_diff < 0:
            # Margin of date estimate error
            score += 8
            signals.append("Timeline Near-Match (±2 days)")
    except Exception:
        pass

    # 4. Description & Title Keywords (35 pts)
    lost_text_words = extract_keywords(f"{lost_item['title']} {lost_item['description']}")
    found_text_words = extract_keywords(f"{found_item['title']} {found_item['description']}")

    overlap = lost_text_words.intersection(found_text_words)
    total_unique = len(lost_text_words.union(found_text_words))

    if total_unique > 0:
        jaccard = len(overlap) / total_unique
        # scale up to 35
        text_score = min(35, int(jaccard * 100 * 1.4))
        if overlap:
            text_score = max(text_score, min(35, len(overlap) * 6))
        score += text_score
        if overlap:
            sample_keywords = list(overlap)[:5]
            signals.append(f"Attribute Overlap: {', '.join(sample_keywords)}")

    # Determine confidence level
    if score >= 65:
        confidence = "High"
    elif score >= 40:
        confidence = "Medium"
    else:
        confidence = "Low"

    # Generate transparent explanation
    if score >= 65:
        explanation = (
            f"Strong correspondence detected between the lost item '{lost_item['title']}' and found item '{found_item['title']}'. "
            f"Both share the '{lost_item['category']}' category, aligned incident timelines, and key descriptive markers "
            f"({', '.join(list(overlap)[:4]) if overlap else 'matching location context'})."
        )
    elif score >= 40:
        explanation = (
            f"Plausible candidate with moderate overlap in category and location. "
            f"Inspect descriptive details and physical identifying marks before proceeding."
        )
    else:
        explanation = (
            f"Low correlation. Differing categories or insufficient descriptive overlap between "
            f"the reported items."
        )

    return {
        'score': min(100, max(0, score)),
        'confidence': confidence,
        'explanation': explanation,
        'signals': "; ".join(signals),
        'source': 'deterministic'
    }

# =========================================================================
# SMART MATCH AGENT (GEMINI PROMPT + FALLBACK)
# =========================================================================

def smart_match_agent(lost_item, found_item):
    """
    Compares a lost item and a found item.
    Uses Gemini API if available, otherwise falls back to deterministic matching.
    """
    client = get_gemini_client()
    if not client:
        return deterministic_match_items(lost_item, found_item)

    prompt = f"""You are the FindBack Smart Match Agent, an expert AI for lost-and-found item correlation.
Analyze the following two listings and determine the likelihood that the Found Item is the same physical object as the Lost Item.

[LOST ITEM LISTING]
Title: {lost_item['title']}
Category: {lost_item['category']}
Reported Loss Date: {lost_item['incident_date']}
Reported Location: {lost_item['location']}
Description: {lost_item['description']}

[FOUND ITEM LISTING]
Title: {found_item['title']}
Category: {found_item['category']}
Reported Found Date: {found_item['incident_date']}
Reported Location: {found_item['location']}
Description: {found_item['description']}

Evaluate using:
1. Description semantic alignment (colors, brands, models, accessories, unique features)
2. Category compatibility
3. Location proximity and spatial probability
4. Date logic (Found date should usually be on or after lost date)

You MUST respond strictly with valid JSON conforming to this schema:
{{
  "score": <integer from 0 to 100>,
  "confidence": "<High | Medium | Low>",
  "explanation": "<2-3 clear sentences explaining why this is or is not a match, highlighting key matching signals>",
  "signals": "<Concise semicolon-separated list of key signals, e.g., Category Match; Location: Central Library; Color/Brand: Apple Blue iPhone>"
}}
Do NOT output any markdown backticks or extra text outside the JSON object.
"""

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
        )
        text = response.text.strip()
        # Clean potential markdown fences if present
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?", "", text)
            text = re.sub(r"```$", "", text).strip()

        data = json.loads(text)
        return {
            'score': int(data.get('score', 50)),
            'confidence': str(data.get('confidence', 'Medium')),
            'explanation': str(data.get('explanation', '')),
            'signals': str(data.get('signals', '')),
            'source': 'ai-gemini'
        }
    except Exception as e:
        print(f"[Smart Match Agent Gemini Error] {e}. Falling back to deterministic matching.")
        fallback = deterministic_match_items(lost_item, found_item)
        fallback['source'] = 'deterministic (AI fallback)'
        return fallback

# =========================================================================
# CLAIM VERIFICATION AGENT (GEMINI PROMPT + FALLBACK)
# =========================================================================

def claim_verification_agent(item, claim):
    """
    Generates tailored verification questions and reviewer guidance for human moderators.
    HUMAN REVIEW IS MANDATORY: AI never automatically approves ownership.
    """
    if hasattr(item, 'keys'):
        item = dict(item)

    client = get_gemini_client()
    if not client:
        return deterministic_claim_verification(item, claim)

    secret_context = f"\nFound Item Secret Details (Known only to finder): {item.get('secret_details', '')}" if item.get('secret_details') else ""

    prompt = f"""You are the FindBack Claim Verification Agent.
Your role is to assist a human reviewer in validating whether the claimant is the genuine owner of a found item.
IMPORTANT POLICY: AI NEVER automatically approves ownership. Human review and physical verification are mandatory.

[FOUND ITEM]
Title: {item['title']}
Category: {item['category']}
Location Found: {item['location']}
Found Date: {item['incident_date']}
Public Description: {item['description']}{secret_context}

[CLAIM SUBMISSION]
Claimant Name: {claim['claimer_name']}
Claimant Proof / Description: {claim['proof_description']}

Tasks:
1. Formulate 3 targeted, non-obvious verification questions the human reviewer should ask the claimant to prove ownership.
2. Provide concise Reviewer Guidance & Caution Notes on what subtle physical details to check before releasing the item.

Respond strictly in valid JSON format:
{{
  "verification_questions": [
    "<Question 1>",
    "<Question 2>",
    "<Question 3>"
  ],
  "reviewer_guidance": "<2-3 actionable advice sentences for the human reviewer>",
  "risk_assessment": "<Low | Medium | High caution>",
  "policy_notice": "HUMAN REVIEW MANDATORY: AI must not approve ownership."
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
        return {
            'verification_questions': data.get('verification_questions', []),
            'reviewer_guidance': data.get('reviewer_guidance', ''),
            'risk_assessment': data.get('risk_assessment', 'Medium caution'),
            'policy_notice': "HUMAN REVIEW MANDATORY: AI must not approve ownership.",
            'source': 'ai-gemini'
        }
    except Exception as e:
        print(f"[Claim Verification Agent Gemini Error] {e}. Using deterministic verification assistant.")
        return deterministic_claim_verification(item, claim)

def deterministic_claim_verification(item, claim):
    """
    Deterministic rule-based verification guidance and targeted questions.
    """
    if hasattr(item, 'keys'):
        item = dict(item)

    category = item.get('category', '').lower()
    title = item.get('title', '').lower()
    secret = item.get('secret_details', '').strip()

    questions = []

    if 'electronics' in category or 'phone' in title or 'laptop' in title:
        questions.append("Can the claimant describe the lock screen wallpaper, case brand, or stickers on the device?")
        questions.append("Can they unlock the device with passcode or biometric verification in person?")
        questions.append("Can they provide the device IMEI, serial number, or matching purchase receipt?")
    elif 'wallet' in category or 'bag' in category:
        questions.append("Can the claimant name specific membership cards, transit passes, or IDs inside?")
        questions.append("Can they describe interior compartment contents or hidden pockets not shown in public listing?")
        questions.append("What specific currency denominations or coins were inside?")
    elif 'key' in category:
        questions.append("How many keys are on the ring and what color/brand is the keychain or carabiner?")
        questions.append("Can they demonstrate that the car remote or door key works on the corresponding lock?")
        questions.append("Are there any barcode tags or distinctive markings on individual keys?")
    else:
        questions.append("Can the claimant provide a photograph of themselves with the item prior to losing it?")
        questions.append("Can they describe any unique scratches, dents, or wear patterns?")
        questions.append("Can they state where and approximately when they were at the location?")

    guidance = (
        f"Cross-reference the claimant's statement with the found item records. "
        + (f"Pay close attention to private verification markers: '{secret}'. " if secret else "Check for unpublicized identifying marks. ")
        + "Request government or student photo ID before physical handoff."
    )

    return {
        'verification_questions': questions,
        'reviewer_guidance': guidance,
        'risk_assessment': 'Standard Review Required',
        'policy_notice': 'HUMAN REVIEW MANDATORY: AI must not approve ownership.',
        'source': 'deterministic'
    }
