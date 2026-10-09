import os
import re
import uuid
import tempfile
from datetime import datetime
from functools import wraps
from urllib.parse import urlparse
from flask import Flask, render_template, request, redirect, url_for, flash, abort, send_from_directory, session, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

import db
import models
import ai_service
import email_service
import assistant_service

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'findback-hackathon-2026-secret')
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# Dedicated local upload storage and size limit (5 MB)
# In serverless environments (e.g. Vercel), app.root_path is read-only, so fallback to writable /tmp
is_serverless_env = bool(os.getenv('VERCEL') or os.getenv('VERCEL_ENV'))
default_upload_dir = os.path.join(app.root_path, 'static', 'uploads')

if is_serverless_env:
    UPLOAD_FOLDER = os.path.join(tempfile.gettempdir(), 'findback_uploads')
else:
    try:
        os.makedirs(default_upload_dir, exist_ok=True)
        # Test directory writability
        probe_file = os.path.join(default_upload_dir, '.probe_write')
        with open(probe_file, 'w') as f:
            f.write('ok')
        os.remove(probe_file)
        UPLOAD_FOLDER = default_upload_dir
    except (OSError, IOError):
        UPLOAD_FOLDER = os.path.join(tempfile.gettempdir(), 'findback_uploads')

try:
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
except Exception:
    pass

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
MAX_UPLOAD_SIZE = 5 * 1024 * 1024  # 5 MB
app.config['MAX_CONTENT_LENGTH'] = 6 * 1024 * 1024  # 6 MB HTTP payload limit

ALLOWED_IMAGE_TYPES = {
    'jpeg': '.jpg',
    'png': '.png',
    'webp': '.webp'
}

def detect_image_format_from_bytes(data: bytes):
    """
    Validates genuine image content using binary magic header signatures.
    Rejects spoofed extensions, HTML, SVG, executables, scripts, etc.
    """
    if len(data) < 12:
        return None
    if data.startswith(b'\xff\xd8\xff'):
        return 'jpeg'
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'webp'
    return None

@app.errorhandler(413)
def request_entity_too_large(error):
    flash("The uploaded photograph exceeds the 5 MB size limit. Please upload a smaller image.", "error")
    return redirect(url_for('report_item'))

# Ensure DB tables exist on app launch without crashing serverless module import
try:
    with app.app_context():
        db.init_db()
        db.seed_db()
except Exception as _startup_db_err:
    print(f"[FindBack Startup] Database initialization notice: {_startup_db_err}")

# Template helper filters
@app.template_filter('status_label')
def status_label_filter(status_key):
    return models.STATUS_CHOICES.get(status_key, status_key.replace('_', ' ').title())

@app.template_filter('format_date')
def format_date_filter(date_str):
    try:
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        return dt.strftime('%b %d, %Y')
    except Exception:
        return date_str

@app.template_filter('item_image')
def item_image_filter(item):
    if not item:
        return 'item-default.svg'
    img = ''
    try:
        if hasattr(item, 'keys') and 'image_filename' in item.keys():
            img = item['image_filename']
    except Exception:
        pass
    if img:
        return img
    title = item['title'] if (hasattr(item, 'keys') and 'title' in item.keys()) else ''
    cat = item['category'] if (hasattr(item, 'keys') and 'category' in item.keys()) else ''
    return models.get_default_image_for_item(title, cat)

@app.template_filter('item_image_src')
def item_image_src_filter(item):
    """
    Returns the web URL for an item's image.
    If the item has a valid uploaded photo present on disk, serves it from /static/uploads/.
    Otherwise, gracefully falls back to the illustrative SVG in /static/images/.
    """
    if not item:
        return url_for('static', filename='images/item-default.svg')

    photo = ''
    try:
        if hasattr(item, 'keys') and 'photo_filename' in item.keys():
            photo = item['photo_filename']
    except Exception:
        pass

    if photo:
        disk_path = os.path.join(UPLOAD_FOLDER, photo)
        bundled_path = os.path.join(app.root_path, 'static', 'uploads', photo)
        if os.path.isfile(disk_path) or os.path.isfile(bundled_path):
            return url_for('serve_uploaded_file', filename=photo)

    # Fallback to illustrative SVG
    svg_filename = item_image_filter(item)
    return url_for('static', filename='images/' + svg_filename)

@app.template_filter('item_is_photo')
def item_is_photo_filter(item):
    """Checks whether the item possesses an actual uploaded photograph on disk."""
    if not item:
        return False
    try:
        if hasattr(item, 'keys') and 'photo_filename' in item.keys():
            photo = item['photo_filename']
            if photo:
                disk_path = os.path.join(UPLOAD_FOLDER, photo)
                bundled_path = os.path.join(app.root_path, 'static', 'uploads', photo)
                return os.path.isfile(disk_path) or os.path.isfile(bundled_path)
    except Exception:
        pass
    return False

@app.route('/static/uploads/<path:filename>')
@app.route('/uploads/<path:filename>')
def serve_uploaded_file(filename):
    """Serves uploaded user photos from writable or bundled storage."""
    if os.path.isfile(os.path.join(UPLOAD_FOLDER, filename)):
        return send_from_directory(UPLOAD_FOLDER, filename)
    bundled_dir = os.path.join(app.root_path, 'static', 'uploads')
    if os.path.isfile(os.path.join(bundled_dir, filename)):
        return send_from_directory(bundled_dir, filename)
    abort(404)

# =========================================================================
# AUTHENTICATION & ACCESS CONTROL HELPERS
# =========================================================================

def get_current_user():
    """Retrieves the currently authenticated user from session, or None."""
    user_id = session.get('user_id')
    if not user_id:
        return None
    return models.get_user_by_id(user_id)

@app.context_processor
def inject_user():
    """Makes current_user available across all Jinja2 templates."""
    return {'current_user': get_current_user()}

def is_safe_url(target):
    """Validates redirection targets to prevent open redirect vulnerabilities."""
    if not target:
        return False
    ref_url = urlparse(request.host_url)
    test_url = urlparse(target)
    return test_url.scheme in ('http', 'https', '') and (test_url.netloc == '' or test_url.netloc == ref_url.netloc)

def login_required(f):
    """Decorator requiring active session authentication."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('user_id'):
            flash("Please sign in to access this page.", "info")
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def reviewer_required(f):
    """Decorator restricting route access strictly to staff reviewers."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('user_id'):
            flash("Please sign in with a reviewer account.", "info")
            return redirect(url_for('login', next=request.url))
        user = get_current_user()
        if not user or user['role'] != 'reviewer':
            flash("Access restricted to staff reviewers only.", "error")
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

# =========================================================================
# ROUTES
# =========================================================================

@app.route('/media/hero.mp4')
def serve_hero_video():
    """Safely serves the cinematic hero background video from the project root."""
    return send_from_directory(app.root_path, 'hero.mp4', mimetype='video/mp4')

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Sign-in page for regular users and reviewers."""
    if get_current_user():
        return redirect(url_for('index'))

    if request.method == 'GET':
        return render_template('auth/login.html', form_data={})

    email = request.form.get('email', '').strip()
    password = request.form.get('password', '')

    if not email or not password:
        flash("Please enter both your email address and password.", "error")
        return render_template('auth/login.html', form_data={'email': email})

    user = models.get_user_by_email(email)
    if not user or not check_password_hash(user['password_hash'], password):
        flash("Invalid email or password. Please verify your credentials.", "error")
        return render_template('auth/login.html', form_data={'email': email})

    session.clear()
    session['user_id'] = user['id']
    session['user_name'] = user['name']
    session['user_email'] = user['email']
    session['user_role'] = user['role']

    # Auto-associate historical records matching verified email
    try:
        models.link_unclaimed_user_records(user['id'], user['email'])
    except Exception:
        pass

    flash(f"Welcome back, {user['name']}!", "success")
    next_url = request.args.get('next')
    if next_url and is_safe_url(next_url):
        return redirect(next_url)
    return redirect(url_for('index'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    """Account creation page."""
    if get_current_user():
        return redirect(url_for('index'))

    if request.method == 'GET':
        return render_template('auth/register.html', form_data={})

    name = request.form.get('name', '').strip()
    email = request.form.get('email', '').strip()
    password = request.form.get('password', '')
    confirm_password = request.form.get('confirm_password', '')

    errors = []
    if len(name) < 2:
        errors.append("Please provide your full name (at least 2 characters).")

    email_regex = r'^[\w\.-]+@[\w\.-]+\.\w+$'
    if not re.match(email_regex, email):
        errors.append("Please provide a valid email address.")

    if len(password) < 8:
        errors.append("Password must be at least 8 characters long.")

    if password != confirm_password:
        errors.append("Passwords do not match. Please re-enter.")

    if not errors and models.get_user_by_email(email):
        errors.append("An account with this email address already exists. Please sign in.")

    if errors:
        for err in errors:
            flash(err, "error")
        return render_template('auth/register.html', form_data={'name': name, 'email': email})

    pwd_hash = generate_password_hash(password)
    user_id = models.create_user(name=name, email=email, password_hash=pwd_hash, role='user')

    session.clear()
    session['user_id'] = user_id
    session['user_name'] = name
    session['user_email'] = email
    session['user_role'] = 'user'

    try:
        models.link_unclaimed_user_records(user_id, email)
    except Exception:
        pass

    flash(f"Account created successfully! Welcome to FindBack, {name}.", "success")
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    """Sign out current user and clear session."""
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for('index'))

@app.route('/activity')
@app.route('/my-activity')
@login_required
def my_activity():
    """
    Dedicated personal activity and history dashboard for the authenticated user.
    Strictly isolated: users can only view their own reports, claims, and matching leads.
    """
    user = get_current_user()
    user_id = user['id']
    user_email = user['email']

    # Auto-link any records matching verified email
    models.link_unclaimed_user_records(user_id, user_email)

    # Active filter tab: 'all', 'lost', 'found', 'claims', 'resolved'
    active_tab = request.args.get('tab', 'all').lower().strip()
    if active_tab not in ('all', 'lost', 'found', 'claims', 'resolved'):
        active_tab = 'all'

    # Fetch user's reports, claims, and potential matches
    user_reports = models.get_user_items(user_id, email=user_email)
    user_claims = models.get_user_claims(user_id, email=user_email)
    user_matches = models.get_user_matches(user_id, email=user_email)

    lost_count = sum(1 for item in user_reports if item['type'] == 'lost')
    found_count = sum(1 for item in user_reports if item['type'] == 'found')
    resolved_count = (
        sum(1 for item in user_reports if item['status'] == 'resolved') +
        sum(1 for claim in user_claims if claim['status'] == 'approved')
    )
    claims_count = len(user_claims)
    total_activity_count = len(user_reports) + len(user_claims)

    # Filter reports & claims according to tab
    filtered_reports = user_reports
    filtered_claims = user_claims

    if active_tab == 'lost':
        filtered_reports = [i for i in user_reports if i['type'] == 'lost']
        filtered_claims = []
    elif active_tab == 'found':
        filtered_reports = [i for i in user_reports if i['type'] == 'found']
        filtered_claims = []
    elif active_tab == 'claims':
        filtered_reports = []
        filtered_claims = user_claims
    elif active_tab == 'resolved':
        filtered_reports = [i for i in user_reports if i['status'] == 'resolved']
        filtered_claims = [c for c in user_claims if c['status'] == 'approved']

    return render_template(
        'activity/index.html',
        user=user,
        active_tab=active_tab,
        reports=filtered_reports,
        claims=filtered_claims,
        matches=user_matches,
        lost_count=lost_count,
        found_count=found_count,
        claims_count=claims_count,
        resolved_count=resolved_count,
        total_activity_count=total_activity_count,
        STATUS_CHOICES=models.STATUS_CHOICES
    )

@app.route('/')
def index():
    """Homepage: stats, recent items, and quick action entry points."""
    stats = models.get_stats()
    recent_lost = models.get_items(item_type='lost', status='open', limit=4)
    recent_found = models.get_items(item_type='found', status='open', limit=4)
    all_categories = models.CATEGORIES
    return render_template('index.html',
                           stats=stats,
                           recent_lost=recent_lost,
                           recent_found=recent_found,
                           categories=all_categories)

@app.route('/items')
def item_list():
    """Browse & filter lost and found listings."""
    item_type = request.args.get('type', '').strip()
    category = request.args.get('category', '').strip()
    status = request.args.get('status', '').strip()
    location = request.args.get('location', '').strip()
    query = request.args.get('q', '').strip()

    items = models.get_items(
        item_type=item_type if item_type in ('lost', 'found') else None,
        category=category or None,
        location=location or None,
        status=status or None,
        query=query or None
    )

    return render_template('items/list.html',
                           items=items,
                           item_type=item_type,
                           selected_category=category,
                           selected_status=status,
                           selected_location=location,
                           query=query,
                           categories=models.CATEGORIES,
                           statuses=models.STATUS_CHOICES)

@app.route('/items/<int:item_id>')
def item_detail(item_id):
    """View details of a single item and its potential matches."""
    item = models.get_item_by_id(item_id)
    if not item:
        abort(404, description="Item not found")

    # Fetch stored matches
    is_lost = (item['type'] == 'lost')
    stored_matches = models.get_matches_for_item(item_id, is_lost=is_lost)

    # If no stored matches yet, automatically find candidates of opposite type
    candidate_matches = []
    if stored_matches:
        candidate_matches = stored_matches
    else:
        opposite_type = 'found' if is_lost else 'lost'
        candidates = models.get_items(item_type=opposite_type, limit=10)
        evaluated = []
        for cand in candidates:
            if is_lost:
                res = ai_service.deterministic_match_items(item, cand)
                cand_id = cand['id']
            else:
                res = ai_service.deterministic_match_items(cand, item)
                cand_id = cand['id']

            if res['score'] >= 35:
                evaluated.append({
                    'candidate_id': cand_id,
                    'candidate_title': cand['title'],
                    'candidate_category': cand['category'],
                    'candidate_location': cand['location'],
                    'candidate_date': cand['incident_date'],
                    'candidate_description': cand['description'],
                    'candidate_type': cand['type'],
                    'candidate_status': cand['status'],
                    'candidate_photo_filename': cand['photo_filename'] if 'photo_filename' in cand.keys() else '',
                    'candidate_image_filename': cand['image_filename'] if 'image_filename' in cand.keys() else '',
                    'score': res['score'],
                    'confidence': res['confidence'],
                    'explanation': res['explanation'],
                    'signals': res['signals'],
                    'source': res['source']
                })
        candidate_matches = sorted(evaluated, key=lambda x: x['score'], reverse=True)

    return render_template('items/detail.html',
                           item=item,
                           matches=candidate_matches,
                           is_lost=is_lost)

@app.route('/items/<int:item_id>/match', methods=['POST'])
def trigger_smart_match(item_id):
    """Triggers the Smart Match Agent for this item against all opposite items."""
    item = models.get_item_by_id(item_id)
    if not item:
        abort(404)

    is_lost = (item['type'] == 'lost')
    opposite_type = 'found' if is_lost else 'lost'
    candidates = models.get_items(item_type=opposite_type, limit=15)

    matches_created = 0
    notifications_sent = 0
    for cand in candidates:
        if is_lost:
            lost_item, found_item = item, cand
        else:
            lost_item, found_item = cand, item

        result = ai_service.smart_match_agent(lost_item, found_item)
        if result['score'] >= 30:
            models.save_or_update_match(
                lost_item_id=lost_item['id'],
                found_item_id=found_item['id'],
                score=result['score'],
                confidence=result['confidence'],
                explanation=result['explanation'],
                signals=result['signals'],
                source=result['source']
            )
            matches_created += 1

        if result['score'] > 60:
            try:
                email_service.send_match_notification(
                    lost_item=lost_item,
                    found_item=found_item,
                    match_score=result['score'],
                    base_url=request.host_url
                )
                notifications_sent += 1
            except Exception as e:
                print(f"[Email Notification Warning] {e}")

    notice = f"Smart Match Agent scan complete: analyzed {len(candidates)} listings and recorded {matches_created} plausible matches."
    if notifications_sent > 0:
        notice += f" High-match email notifications evaluated for {notifications_sent} candidate(s)."
    flash(notice, "success")
    return redirect(url_for('item_detail', item_id=item_id))

@app.route('/report', methods=['GET', 'POST'])
def report_item():
    """Report a Lost or Found item with strict server-side validation."""
    if request.method == 'GET':
        initial_type = request.args.get('type', 'lost')
        if initial_type not in ('lost', 'found'):
            initial_type = 'lost'
        form_defaults = {}
        curr_user = get_current_user()
        if curr_user:
            form_defaults['contact_name'] = curr_user['name']
            form_defaults['contact_email'] = curr_user['email']
        return render_template('items/report.html',
                               item_type=initial_type,
                               categories=models.CATEGORIES,
                               form_data=form_defaults)

    # POST handling
    item_type = request.form.get('type', '').strip()
    title = request.form.get('title', '').strip()
    category = request.form.get('category', '').strip()
    description = request.form.get('description', '').strip()
    location = request.form.get('location', '').strip()
    incident_date = request.form.get('incident_date', '').strip()
    contact_name = request.form.get('contact_name', '').strip()
    contact_email = request.form.get('contact_email', '').strip()
    contact_phone = request.form.get('contact_phone', '').strip()
    secret_details = request.form.get('secret_details', '').strip()

    errors = []

    if item_type not in ('lost', 'found'):
        errors.append("Please specify whether you are reporting a Lost or Found item.")

    if len(title) < 3:
        errors.append("Item title must be at least 3 characters long.")

    if category not in models.CATEGORIES:
        errors.append("Please select a valid item category from the list.")

    if len(description) < 10:
        errors.append("Please provide a descriptive explanation (minimum 10 characters).")

    if not location:
        errors.append("Please provide the location where the item was lost or found.")

    if not incident_date:
        errors.append("Please specify the date of the incident.")
    else:
        try:
            parsed_date = datetime.strptime(incident_date, '%Y-%m-%d')
            if parsed_date > datetime.now():
                errors.append("Incident date cannot be in the future.")
        except ValueError:
            errors.append("Invalid date format. Please use YYYY-MM-DD.")

    if len(contact_name) < 2:
        errors.append("Contact name is required.")

    email_regex = r'^[\w\.-]+@[\w\.-]+\.\w+$'
    if not re.match(email_regex, contact_email):
        errors.append("Please provide a valid contact email address.")

    # Photograph file handling
    uploaded_photo_filename = ''
    photo_file = request.files.get('photo')
    if photo_file and photo_file.filename and photo_file.filename.strip():
        photo_bytes = photo_file.read()
        if len(photo_bytes) > MAX_UPLOAD_SIZE:
            errors.append("The uploaded photograph exceeds the 5 MB limit. Please upload an image under 5 MB.")
        elif len(photo_bytes) == 0:
            errors.append("The uploaded photograph file is empty.")
        else:
            img_format = detect_image_format_from_bytes(photo_bytes)
            if not img_format:
                errors.append("Invalid image file. Only genuine JPEG, PNG, and WebP photographs are supported.")
            else:
                ext = ALLOWED_IMAGE_TYPES[img_format]
                uploaded_photo_filename = f"photo_{uuid.uuid4().hex}{ext}"
                target_path = os.path.join(UPLOAD_FOLDER, uploaded_photo_filename)
                with open(target_path, 'wb') as f:
                    f.write(photo_bytes)

    if errors:
        # If any validation failed, clean up any uploaded temporary file
        if uploaded_photo_filename:
            cleanup_path = os.path.join(UPLOAD_FOLDER, uploaded_photo_filename)
            if os.path.exists(cleanup_path):
                try:
                    os.remove(cleanup_path)
                except Exception:
                    pass

        for err in errors:
            flash(err, "error")
        return render_template('items/report.html',
                               item_type=item_type,
                               categories=models.CATEGORIES,
                               form_data=request.form)

    # Validated: save to DB
    new_item_id = models.create_item({
        'user_id': session.get('user_id'),
        'type': item_type,
        'title': title,
        'category': category,
        'description': description,
        'location': location,
        'incident_date': incident_date,
        'contact_name': contact_name,
        'contact_email': contact_email,
        'contact_phone': contact_phone,
        'secret_details': secret_details,
        'photo_filename': uploaded_photo_filename
    })

    # Evaluate potential matches and trigger notifications for high-confidence candidates (> 60%)
    try:
        new_item = models.get_item_by_id(new_item_id)
        is_lost = (item_type == 'lost')
        opposite_type = 'found' if is_lost else 'lost'
        candidates = models.get_items(item_type=opposite_type, limit=15)
        for cand in candidates:
            lost_cand = new_item if is_lost else cand
            found_cand = cand if is_lost else new_item
            res = ai_service.smart_match_agent(lost_cand, found_cand)
            if res['score'] >= 30:
                models.save_or_update_match(
                    lost_item_id=lost_cand['id'],
                    found_item_id=found_cand['id'],
                    score=res['score'],
                    confidence=res['confidence'],
                    explanation=res['explanation'],
                    signals=res['signals'],
                    source=res['source']
                )
            if res['score'] > 60:
                email_service.send_match_notification(
                    lost_item=lost_cand,
                    found_item=found_cand,
                    match_score=res['score'],
                    base_url=request.host_url
                )
    except Exception as e:
        print(f"[Match/Notification Evaluation Notice] {e}")

    flash(f"Successfully posted your {item_type.title()} item report! Our Smart Match Agent is checking for candidates.", "success")
    return redirect(url_for('item_detail', item_id=new_item_id))

@app.route('/items/<int:item_id>/claim', methods=['GET', 'POST'])
def submit_claim(item_id):
    """Submit an ownership claim for a found item."""
    item = models.get_item_by_id(item_id)
    if not item:
        abort(404)

    if item['type'] != 'found':
        flash("Claims can only be filed against found items.", "warning")
        return redirect(url_for('item_detail', item_id=item_id))

    if item['status'] == 'resolved':
        flash("This item has already been marked resolved and returned to its owner.", "info")
        return redirect(url_for('item_detail', item_id=item_id))

    if request.method == 'GET':
        form_defaults = {}
        curr_user = get_current_user()
        if curr_user:
            form_defaults = {
                'claimer_name': curr_user['name'],
                'claimer_email': curr_user['email']
            }
        return render_template('claims/submit.html', item=item, form_data=form_defaults)

    # POST handling
    claimer_name = request.form.get('claimer_name', '').strip()
    claimer_email = request.form.get('claimer_email', '').strip()
    claimer_phone = request.form.get('claimer_phone', '').strip()
    proof_description = request.form.get('proof_description', '').strip()

    errors = []
    if len(claimer_name) < 2:
        errors.append("Claimant full name is required.")

    email_regex = r'^[\w\.-]+@[\w\.-]+\.\w+$'
    if not re.match(email_regex, claimer_email):
        errors.append("Please provide a valid contact email address.")

    if len(proof_description) < 15:
        errors.append("Please describe your proof of ownership in detail (at least 15 characters). Mention unique marks, contents, or serial numbers.")

    if errors:
        for err in errors:
            flash(err, "error")
        return render_template('claims/submit.html', item=item, form_data=request.form)

    # Call Claim Verification Agent to generate verification questions & reviewer guidance
    verification_pack = ai_service.claim_verification_agent(
        item=item,
        claim={'claimer_name': claimer_name, 'proof_description': proof_description}
    )

    questions_formatted = "\n".join(f"- {q}" for q in verification_pack['verification_questions'])
    ai_notes = (
        f"SOURCE: {verification_pack['source'].upper()}\n"
        f"RISK/CAUTION: {verification_pack['risk_assessment']}\n"
        f"POLICY: {verification_pack['policy_notice']}\n\n"
        f"SUGGESTED VERIFICATION QUESTIONS FOR REVIEWER:\n{questions_formatted}\n\n"
        f"REVIEWER GUIDANCE:\n{verification_pack['reviewer_guidance']}"
    )

    claim_id = models.create_claim({
        'item_id': item_id,
        'user_id': session.get('user_id'),
        'claimer_name': claimer_name,
        'claimer_email': claimer_email,
        'claimer_phone': claimer_phone,
        'proof_description': proof_description,
        'ai_verification_notes': ai_notes
    })

    flash("Your ownership claim has been submitted! It is now pending mandatory human verification by our staff.", "success")
    return redirect(url_for('item_detail', item_id=item_id))

@app.route('/matches')
def matches_hub():
    """Smart Match Hub: overview of cross-match pairings and interactive manual matcher."""
    # Fetch all lost items and found items for pairwise explorer
    lost_items = models.get_items(item_type='lost', status='open')
    found_items = models.get_items(item_type='found', status='open')

    # Quick comparison if query params supplied
    compare_lost_id = request.args.get('lost_id', type=int)
    compare_found_id = request.args.get('found_id', type=int)
    comparison_result = None

    if compare_lost_id and compare_found_id:
        lost_item = models.get_item_by_id(compare_lost_id)
        found_item = models.get_item_by_id(compare_found_id)
        if lost_item and found_item:
            comparison_result = ai_service.smart_match_agent(lost_item, found_item)
            comparison_result['lost_item'] = lost_item
            comparison_result['found_item'] = found_item

    return render_template('matches/index.html',
                           lost_items=lost_items,
                           found_items=found_items,
                           compare_lost_id=compare_lost_id,
                           compare_found_id=compare_found_id,
                           comparison_result=comparison_result)

@app.route('/reviewer')
@reviewer_required
def reviewer_dashboard():
    """Human Reviewer Portal: mandatory human review of all ownership claims."""
    status_filter = request.args.get('status', '').strip()
    claims = models.get_claims(status=status_filter or None)
    return render_template('reviewer/dashboard.html',
                           claims=claims,
                           selected_status=status_filter,
                           total_claims=len(claims))

@app.route('/reviewer/claims/<int:claim_id>')
@reviewer_required
def review_claim_detail(claim_id):
    """Detailed claim review page with Claim Verification Agent questions and decision form."""
    claim = models.get_claim_by_id(claim_id)
    if not claim:
        abort(404, description="Claim not found")

    return render_template('claims/detail.html', claim=claim)

@app.route('/reviewer/claims/<int:claim_id>/decision', methods=['POST'])
@reviewer_required
def claim_decision(claim_id):
    """Reviewer submits decision: approve, reject, or request more information."""
    claim = models.get_claim_by_id(claim_id)
    if not claim:
        abort(404)

    decision = request.form.get('decision', '').strip()
    reviewer_notes = request.form.get('reviewer_notes', '').strip()

    if decision not in ('approved', 'rejected', 'more_info_needed'):
        flash("Invalid decision selected.", "error")
        return redirect(url_for('review_claim_detail', claim_id=claim_id))

    models.update_claim_decision(claim_id, decision, reviewer_notes)

    status_messages = {
        'approved': "Claim approved! The item status has been updated to Resolved & Returned.",
        'rejected': "Claim rejected. The found item remains active for other claimants.",
        'more_info_needed': "Claim marked as 'More Info Needed'. Review notes updated."
    }

    flash(status_messages.get(decision, "Claim status updated."), "success")
    return redirect(url_for('reviewer_dashboard'))

# ============================================================================
# FINDBACK ASSISTANT ROUTES & APIS
# ============================================================================

@app.route('/assistant')
def assistant_page():
    """Dedicated full-page interface for FindBack Assistant."""
    item_id = request.args.get('item_id', type=int)
    context_item = None
    if item_id:
        context_item = models.get_item_by_id(item_id)
    return render_template('assistant/index.html', context_item=context_item)

@app.route('/api/assistant/chat', methods=['POST'])
def assistant_chat_api():
    """
    Asynchronous JSON chat endpoint for FindBack Assistant.
    Accepts: { 'message': str, 'context': dict (optional) }
    Returns: { 'status': 'success', 'reply': str, 'actions': list }
    """
    if not request.is_json:
        return jsonify({
            'status': 'error',
            'error': 'Request payload must be application/json.'
        }), 400

    data = request.get_json(silent=True) or {}
    user_message = data.get('message', '')

    if not isinstance(user_message, str):
        return jsonify({
            'status': 'error',
            'error': 'Message field must be a valid text string.'
        }), 400

    user_message = user_message.strip()
    if not user_message:
        return jsonify({
            'status': 'error',
            'error': 'Message cannot be empty.'
        }), 400

    # Reasonable input length limitation to prevent prompt stuffing and abuse
    if len(user_message) > 600:
        return jsonify({
            'status': 'error',
            'error': 'Message exceeds maximum length of 600 characters. Please provide a more concise inquiry.'
        }), 400

    # Optional page context (e.g. current item ID from item detail page)
    page_context = data.get('context')
    if not isinstance(page_context, dict):
        page_context = {}

    # Inject server-side authenticated identity (never trust client-supplied auth)
    curr_user = get_current_user()
    page_context['auth'] = {
        'logged_in': bool(curr_user),
        'user_id': curr_user['id'] if curr_user else None,
        'user_name': curr_user['name'] if curr_user else None,
        'user_email': curr_user['email'] if curr_user else None,
        'user_role': curr_user['role'] if curr_user else None
    }

    # Retrieve and cap session-based conversation history (max 8 turns = 16 messages)
    history = session.get('assistant_history', [])
    if not isinstance(history, list):
        history = []
    history = history[-16:]

    try:
        res = assistant_service.process_assistant_message(
            user_message=user_message,
            conversation_history=history,
            page_context=page_context
        )
    except Exception:
        res = assistant_service.deterministic_assistant_reply(
            user_message,
            relevant_items=None,
            page_context=page_context
        )

    # Safely extract reply and actions from dict response
    if isinstance(res, dict):
        reply_text = res.get('reply')
        actions = res.get('actions')
    elif isinstance(res, (tuple, list)) and len(res) >= 2:
        reply_text, actions = res[0], res[1]
    else:
        reply_text = str(res)
        actions = []

    # Never display a JSON property name or placeholder such as 'reply'
    if not reply_text or str(reply_text).strip().lower() in ('reply', 'none', 'undefined', 'null'):
        fallback = assistant_service.deterministic_assistant_reply(
            user_message,
            relevant_items=None,
            page_context=page_context
        )
        reply_text = fallback.get('reply', 'Please describe the item you lost, or browse existing reports.')
        if not actions:
            actions = fallback.get('actions', [])

    if not isinstance(actions, list):
        actions = []

    # Update session history
    history.append({'role': 'user', 'content': user_message})
    history.append({'role': 'assistant', 'content': reply_text})
    session['assistant_history'] = history[-16:]
    session.modified = True

    return jsonify({
        'status': 'success',
        'reply': reply_text,
        'actions': actions
    }), 200

@app.route('/api/assistant/clear', methods=['POST'])
def assistant_clear_api():
    """Clears the session-backed conversational history for FindBack Assistant."""
    session['assistant_history'] = []
    session.modified = True
    return jsonify({
        'status': 'success',
        'message': 'Conversation cleared successfully.'
    }), 200

@app.errorhandler(404)
def page_not_found(e):
    return render_template('base.html', not_found=True), 404

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
