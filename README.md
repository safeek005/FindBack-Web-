# FindBack AI — Lost-and-Found Community Platform

**Hackathon Problem #34 Solution**

FindBack AI is a community lost-and-found web platform that pairs intelligent AI matching with rigorous human claim verification.

---

## 🌟 Core Features & Differentiators

1. **Smart Match Agent**:
   - Compares lost and found listings using multi-signal correlation:
     - **Semantic Description Overlap**: Key descriptive markers (colors, models, materials, accessories).
     - **Category Match**: Ensures object compatibility.
     - **Location Proximity**: Matches campus/city landmarks and zones.
     - **Timeline Logic**: Analyzes chronological date plausibility (found date relative to lost date).
   - Generates transparent match explanations with confidence scores (0-100%) and detected signal tags.
   - Built with Gemini API (`gemini-2.5-flash`), with an intelligent rule-based deterministic fallback when offline or without an API key.

2. **Claim Verification Agent**:
   - Assisting human moderators during ownership claims.
   - Generates 3 targeted, non-obvious verification questions (e.g. lock screen wallpaper, case stickers, subtle scratches, hidden card contents, serial number fragments).
   - Provides risk assessments and reviewer caution checklists.

3. **Mandatory Human Review**:
   - Strict policy: **AI NEVER automatically approves ownership**.
   - Staff reviewers make all final decisions (Approve & Reunited, Reject, Request More Info) with audit notes.

4. **Confidential Secret Details**:
   - Reporters can specify private identifying marks that are kept hidden from public view to prevent fraudulent claims.

5. **Pure HTML & CSS (Zero Client-Side JavaScript)**:
   - 100% compliant with the hackathon frontend restriction.
   - Pure server-rendered Jinja2 templates and standard HTTP forms.
   - Rich, modern dark-mode aesthetic with custom design tokens, responsive CSS Grid/Flexbox, and subtle CSS transitions.

---

## 🛠️ Tech Stack

- **Backend**: Python 3.14 + Flask
- **Database**: SQLite (`findback.db`)
- **AI Provider**: Google Gemini API via `google-genai` (with deterministic fallback)
- **Frontend**: Server-rendered HTML5 + Vanilla CSS (No client-side JS)
- **Environment**: Configuration via `.env` (API key excluded from version control)

---

## 🚀 Getting Started

### 1. Install Dependencies
```bash
python -m pip install -r requirements.txt
```

### 2. Configure Environment (Optional)
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Add your `GEMINI_API_KEY` in `.env` if you want to use live Gemini API calls. If left empty, FindBack AI smoothly defaults to its intelligent deterministic rule-based matcher.

### 3. Initialize & Seed Database
```bash
python db.py
```
*(Pre-loaded with realistic fictional lost-and-found scenarios ready for testing).*

### 4. Run the Application
```bash
python app.py
```
Open [http://127.0.0.1:5000](http://127.0.0.1:5000) in your browser.

---

## 🧪 Automated Test Suite
Run the full test suite verifying all routes, persistence, forms, and matching:
```bash
python test_app.py
```

---

## 📁 Project Structure

```
findback-web/
├── .env.example            # Environment configuration template
├── .gitignore              # Ignores .env, *.db, and pycache
├── requirements.txt        # Flask, google-genai, python-dotenv
├── README.md               # Documentation and problem summary
├── app.py                  # Main Flask routes and controllers
├── db.py                   # SQLite database schema and seed data
├── models.py               # Data models and business logic queries
├── ai_service.py           # Smart Match & Claim Verification Agents + Fallback
├── test_app.py             # Automated unit and integration tests
├── static/
│   └── css/
│       └── style.css       # Modern CSS design system
└── templates/
    ├── base.html           # Base layout, navbar, flash messages, footer
    ├── index.html          # Homepage with stats and spotlights
    ├── items/
    │   ├── list.html       # Search and filter listings
    │   ├── detail.html     # Item details and Smart Match panel
    │   └── report.html     # Report lost or found item form
    ├── matches/
    │   └── index.html      # Smart Match Hub & pairwise comparator
    ├── claims/
    │   ├── submit.html     # Claim submission form
    │   └── detail.html     # Detailed claim review and decision
    └── reviewer/
        └── dashboard.html  # Reviewer Desk: queue of all claims
```
