from db import get_db
from datetime import datetime

# Categories used across the platform
CATEGORIES = [
    'Electronics',
    'Wallets & Bags',
    'Keys',
    'Documents & IDs',
    'Jewelry & Watches',
    'Apparel & Accessories',
    'Books & Notebooks',
    'Pets',
    'Other'
]

STATUS_CHOICES = {
    'open': 'Active / Unclaimed',
    'claim_pending': 'Claim Under Review',
    'resolved': 'Resolved & Returned',
    'closed': 'Closed'
}

def get_stats():
    """Returns platform counters for the homepage banner and summary stats."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM items")
    total_items = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM items WHERE type = 'lost' AND status != 'resolved'")
    lost_items = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM items WHERE type = 'found' AND status != 'resolved'")
    found_items = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM items WHERE status = 'resolved'")
    resolved_items = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM claims WHERE status = 'pending'")
    pending_claims = cursor.fetchone()[0]

    conn.close()
    return {
        'total_items': total_items,
        'lost_items': lost_items,
        'found_items': found_items,
        'resolved_items': resolved_items,
        'pending_claims': pending_claims
    }

def get_items(item_type=None, category=None, query=None, location=None, status=None, limit=None):
    """Retrieve filtered items from the database."""
    conn = get_db()
    cursor = conn.cursor()

    conditions = []
    params = []

    if item_type and item_type in ('lost', 'found'):
        conditions.append("type = ?")
        params.append(item_type)

    if category and category.strip():
        conditions.append("category = ?")
        params.append(category.strip())

    if status and status.strip():
        conditions.append("status = ?")
        params.append(status.strip())

    if location and location.strip():
        conditions.append("location LIKE ?")
        params.append(f"%{location.strip()}%")

    if query and query.strip():
        conditions.append("(title LIKE ? OR description LIKE ?)")
        search_pattern = f"%{query.strip()}%"
        params.extend([search_pattern, search_pattern])

    where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
    sql = f"SELECT * FROM items {where_clause} ORDER BY created_at DESC"

    if limit and isinstance(limit, int):
        sql += f" LIMIT {limit}"

    cursor.execute(sql, params)
    items = cursor.fetchall()
    conn.close()
    return items

def get_item_by_id(item_id):
    """Get single item with its details."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM items WHERE id = ?", (item_id,))
    item = cursor.fetchone()
    conn.close()
    return item

def get_default_image_for_item(title='', category=''):
    """Returns appropriate illustrative image filename based on title or category."""
    t = title.lower()
    c = category.lower()

    # 1. Check specific title keywords first
    if any(k in t for k in ['backpack', 'borealis', 'duffel', 'knapsack', 'tote', 'schoolbag']):
        return 'backpack.svg'
    if any(k in t for k in ['phone', 'iphone', 'android', 'pixel', 'samsung', 'smartphone']):
        return 'smartphone.svg'
    if any(k in t for k in ['headphone', 'earbud', 'airpod', 'audio', 'sony wh', 'sony wf']):
        return 'headphones.svg'
    if any(k in t for k in ['wallet', 'purse', 'bifold', 'cardholder']):
        return 'wallet.svg'
    if any(k in t for k in ['key', 'carabiner', 'fob', 'keychain']):
        return 'keys.svg'
    if any(k in t for k in ['glasses', 'eyewear', 'sunglasses', 'frame', 'spectacles']):
        return 'glasses.svg'
    if any(k in t for k in ['bottle', 'flask', 'tumbler', 'hydro']):
        return 'water-bottle.svg'
    if any(k in t for k in ['laptop', 'macbook', 'notebook computer', 'thinkpad']):
        return 'laptop.svg'
    if any(k in t for k in ['watch', 'smartwatch', 'rolex', 'fitbit']):
        return 'watch.svg'
    if any(k in t for k in ['notebook', 'binder', 'journal', 'textbook', 'book']):
        return 'notebook.svg'

    # 2. Check category broad matches
    if 'key' in c:
        return 'keys.svg'
    if 'wallet' in c:
        return 'wallet.svg'
    if 'apparel' in c:
        return 'glasses.svg'
    if 'electronic' in c:
        return 'smartphone.svg'
    if 'book' in c:
        return 'notebook.svg'

    return 'item-default.svg'

def create_item(data):
    """Inserts a new lost or found item."""
    conn = get_db()
    cursor = conn.cursor()
    image_filename = data.get('image_filename', '').strip()
    photo_filename = data.get('photo_filename', '').strip()
    user_id = data.get('user_id')
    if not image_filename:
        image_filename = get_default_image_for_item(data.get('title', ''), data.get('category', ''))

    cursor.execute("""
    INSERT INTO items (
        user_id, type, title, category, description, location, incident_date,
        contact_name, contact_email, contact_phone, secret_details,
        image_filename, photo_filename, status
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        user_id,
        data['type'],
        data['title'].strip(),
        data['category'].strip(),
        data['description'].strip(),
        data['location'].strip(),
        data['incident_date'].strip(),
        data['contact_name'].strip(),
        data['contact_email'].strip(),
        data.get('contact_phone', '').strip(),
        data.get('secret_details', '').strip(),
        image_filename,
        photo_filename,
        'open'
    ))
    new_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return new_id

def update_item_status(item_id, new_status):
    """Updates the status of an item."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE items SET status = ? WHERE id = ?", (new_status, item_id))
    conn.commit()
    conn.close()

def create_claim(data):
    """Submits a claim for a found item."""
    conn = get_db()
    cursor = conn.cursor()
    user_id = data.get('user_id')
    cursor.execute("""
    INSERT INTO claims (
        item_id, user_id, claimer_name, claimer_email, claimer_phone,
        proof_description, status, reviewer_notes, ai_verification_notes
    ) VALUES (?, ?, ?, ?, ?, ?, 'pending', '', ?)
    """, (
        data['item_id'],
        user_id,
        data['claimer_name'].strip(),
        data['claimer_email'].strip(),
        data.get('claimer_phone', '').strip(),
        data['proof_description'].strip(),
        data.get('ai_verification_notes', '').strip()
    ))
    claim_id = cursor.lastrowid

    # Update item status to 'claim_pending' if currently open
    cursor.execute("UPDATE items SET status = 'claim_pending' WHERE id = ? AND status = 'open'", (data['item_id'],))

    conn.commit()
    conn.close()
    return claim_id

def get_claims(status=None):
    """Fetches claims joined with item information for reviewer dashboard."""
    conn = get_db()
    cursor = conn.cursor()
    sql = """
    SELECT c.*, i.title as item_title, i.category as item_category, i.location as item_location,
           i.type as item_type, i.description as item_description, i.secret_details as item_secret_details,
           i.photo_filename as item_photo_filename, i.image_filename as item_image_filename
    FROM claims c
    JOIN items i ON c.item_id = i.id
    """
    params = []
    if status:
        sql += " WHERE c.status = ?"
        params.append(status)
    sql += " ORDER BY c.created_at DESC"

    cursor.execute(sql, params)
    claims = cursor.fetchall()
    conn.close()
    return claims

def get_claim_by_id(claim_id):
    """Fetches a single claim joined with item details."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT c.*, i.title as item_title, i.category as item_category, i.location as item_location,
           i.type as item_type, i.description as item_description, i.secret_details as item_secret_details,
           i.incident_date as item_incident_date, i.contact_name as item_contact_name, i.contact_email as item_contact_email,
           i.photo_filename as item_photo_filename, i.image_filename as item_image_filename
    FROM claims c
    JOIN items i ON c.item_id = i.id
    WHERE c.id = ?
    """, (claim_id,))
    claim = cursor.fetchone()
    conn.close()
    return claim

def update_claim_decision(claim_id, status, reviewer_notes):
    """Human reviewer updates claim decision. AI never automatically approves ownership."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    UPDATE claims
    SET status = ?, reviewer_notes = ?, reviewed_at = CURRENT_TIMESTAMP
    WHERE id = ?
    """, (status, reviewer_notes.strip(), claim_id))

    # Retrieve the item_id for this claim
    cursor.execute("SELECT item_id FROM claims WHERE id = ?", (claim_id,))
    row = cursor.fetchone()
    if row:
        item_id = row[0]
        if status == 'approved':
            # Mark item resolved / returned
            cursor.execute("UPDATE items SET status = 'resolved' WHERE id = ?", (item_id,))
        elif status == 'rejected':
            # Revert item to open if no other pending claims
            cursor.execute("SELECT COUNT(*) FROM claims WHERE item_id = ? AND status = 'pending'", (item_id,))
            remaining = cursor.fetchone()[0]
            if remaining == 0:
                cursor.execute("UPDATE items SET status = 'open' WHERE id = ? AND status = 'claim_pending'", (item_id,))

    conn.commit()
    conn.close()

def save_or_update_match(lost_item_id, found_item_id, score, confidence, explanation, signals, source='ai'):
    """Saves or updates match evaluation."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO matches (lost_item_id, found_item_id, score, confidence, explanation, signals, source)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(lost_item_id, found_item_id) DO UPDATE SET
        score = excluded.score,
        confidence = excluded.confidence,
        explanation = excluded.explanation,
        signals = excluded.signals,
        source = excluded.source,
        created_at = CURRENT_TIMESTAMP
    """, (lost_item_id, found_item_id, score, confidence, explanation, signals, source))
    conn.commit()
    conn.close()

def get_matches_for_item(item_id, is_lost=True):
    """Retrieves saved matches for a given item."""
    conn = get_db()
    cursor = conn.cursor()
    if is_lost:
        sql = """
        SELECT m.*, i.title as candidate_title, i.category as candidate_category,
               i.location as candidate_location, i.incident_date as candidate_date,
               i.description as candidate_description, i.id as candidate_id, i.type as candidate_type,
               i.status as candidate_status, i.photo_filename as candidate_photo_filename,
               i.image_filename as candidate_image_filename
        FROM matches m
        JOIN items i ON m.found_item_id = i.id
        WHERE m.lost_item_id = ?
        ORDER BY m.score DESC
        """
    else:
        sql = """
        SELECT m.*, i.title as candidate_title, i.category as candidate_category,
               i.location as candidate_location, i.incident_date as candidate_date,
               i.description as candidate_description, i.id as candidate_id, i.type as candidate_type,
               i.status as candidate_status, i.photo_filename as candidate_photo_filename,
               i.image_filename as candidate_image_filename
        FROM matches m
        JOIN items i ON m.lost_item_id = i.id
        WHERE m.found_item_id = ?
        ORDER BY m.score DESC
        """
    cursor.execute(sql, (item_id,))
    matches = cursor.fetchall()
    conn.close()
    return matches

# =========================================================================
# USER ACCOUNT MANAGEMENT
# =========================================================================

def create_user(name, email, password_hash, role='user'):
    """Creates a new registered user account."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO users (name, email, password_hash, role)
    VALUES (?, ?, ?, ?)
    """, (name.strip(), email.strip().lower(), password_hash, role))
    user_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return user_id

def get_user_by_id(user_id):
    """Retrieves a user by ID."""
    if not user_id:
        return None
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    user = cursor.fetchone()
    conn.close()
    return user

def get_user_by_email(email):
    """Retrieves a user by email (case-insensitive)."""
    if not email:
        return None
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE LOWER(email) = LOWER(?)", (email.strip(),))
    user = cursor.fetchone()
    conn.close()
    return user

# =========================================================================
# NOTIFICATION RECORDS & DEDUPLICATION
# =========================================================================

def record_notification(lost_item_id, found_item_id, recipient_email, recipient_name, match_score, status, error_message=''):
    """Records match notification delivery status with deduplication constraint."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO notifications (
        lost_item_id, found_item_id, recipient_email, recipient_name,
        match_score, status, error_message
    ) VALUES (?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(lost_item_id, found_item_id, recipient_email) DO UPDATE SET
        match_score = excluded.match_score,
        status = excluded.status,
        error_message = excluded.error_message,
        created_at = CURRENT_TIMESTAMP
    """, (
        lost_item_id,
        found_item_id,
        recipient_email.strip().lower(),
        recipient_name.strip(),
        match_score,
        status,
        error_message[:500] if error_message else ''
    ))
    notif_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return notif_id

def has_notification_been_sent(lost_item_id, found_item_id, recipient_email):
    """Checks whether a notification has already been successfully sent for this pair & recipient."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT id, match_score FROM notifications
    WHERE lost_item_id = ? AND found_item_id = ? AND LOWER(recipient_email) = LOWER(?) AND status = 'sent'
    """, (lost_item_id, found_item_id, recipient_email.strip()))
    row = cursor.fetchone()
    conn.close()
    return row is not None

# =========================================================================
# PERSONAL USER ACTIVITY & HISTORY QUERIES
# =========================================================================

def link_unclaimed_user_records(user_id, email):
    """
    Safely associates unlinked historical items and claims with the authenticated user ID
    based on verified email identity. Preserves older records without owner ID.
    """
    if not user_id or not email:
        return
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE items
        SET user_id = ?
        WHERE (user_id IS NULL OR user_id = 0) AND LOWER(contact_email) = LOWER(?)
    """, (user_id, email.strip()))
    cursor.execute("""
        UPDATE claims
        SET user_id = ?
        WHERE (user_id IS NULL OR user_id = 0) AND LOWER(claimer_email) = LOWER(?)
    """, (user_id, email.strip()))
    conn.commit()
    conn.close()

def get_user_items(user_id, email=None, item_type=None, status=None):
    """
    Retrieves lost and found items reported by the authenticated user.
    Uses server-side identity (user_id and optional email fallback).
    """
    if not user_id:
        return []
    conn = get_db()
    cursor = conn.cursor()

    conditions = ["(user_id = ? OR (user_id IS NULL AND LOWER(contact_email) = LOWER(?)))"]
    params = [user_id, email.strip() if email else '']

    if item_type and item_type in ('lost', 'found'):
        conditions.append("type = ?")
        params.append(item_type)

    if status and status.strip():
        conditions.append("status = ?")
        params.append(status.strip())

    sql = f"SELECT * FROM items WHERE {' AND '.join(conditions)} ORDER BY created_at DESC"
    cursor.execute(sql, params)
    items = cursor.fetchall()
    conn.close()
    return items

def get_user_claims(user_id, email=None, status=None):
    """
    Retrieves ownership claims filed by the authenticated user.
    Joins item information while strictly omitting internal reviewer ai_verification_notes.
    """
    if not user_id:
        return []
    conn = get_db()
    cursor = conn.cursor()

    conditions = ["(c.user_id = ? OR (c.user_id IS NULL AND LOWER(c.claimer_email) = LOWER(?)))"]
    params = [user_id, email.strip() if email else '']

    if status and status.strip():
        conditions.append("c.status = ?")
        params.append(status.strip())

    sql = f"""
    SELECT c.id, c.item_id, c.user_id, c.claimer_name, c.claimer_email, c.claimer_phone,
           c.proof_description, c.status, c.reviewer_notes, c.created_at, c.reviewed_at,
           i.title as item_title, i.category as item_category, i.location as item_location,
           i.type as item_type, i.status as item_status, i.photo_filename as item_photo_filename,
           i.image_filename as item_image_filename, i.incident_date as item_incident_date
    FROM claims c
    JOIN items i ON c.item_id = i.id
    WHERE {' AND '.join(conditions)}
    ORDER BY c.created_at DESC
    """
    cursor.execute(sql, params)
    claims = cursor.fetchall()
    conn.close()
    return claims

def get_user_matches(user_id, email=None):
    """
    Retrieves algorithmic potential matches involving any report owned by the user.
    Clearly marks candidates as algorithmic leads, not verified ownership.
    """
    if not user_id:
        return []
    conn = get_db()
    cursor = conn.cursor()

    email_clean = email.strip() if email else ''
    sql = """
    SELECT m.id as match_id, m.score, m.confidence, m.explanation, m.signals, m.created_at as match_created_at,
           lost_i.id as lost_id, lost_i.title as lost_title, lost_i.category as lost_category,
           lost_i.location as lost_location, lost_i.incident_date as lost_date, lost_i.status as lost_status,
           lost_i.photo_filename as lost_photo, lost_i.image_filename as lost_image,
           lost_i.user_id as lost_user_id, lost_i.contact_email as lost_email,
           found_i.id as found_id, found_i.title as found_title, found_i.category as found_category,
           found_i.location as found_location, found_i.incident_date as found_date, found_i.status as found_status,
           found_i.photo_filename as found_photo, found_i.image_filename as found_image,
           found_i.user_id as found_user_id, found_i.contact_email as found_email
    FROM matches m
    JOIN items lost_i ON m.lost_item_id = lost_i.id
    JOIN items found_i ON m.found_item_id = found_i.id
    WHERE (
        lost_i.user_id = ? OR LOWER(lost_i.contact_email) = LOWER(?)
        OR found_i.user_id = ? OR LOWER(found_i.contact_email) = LOWER(?)
    )
    ORDER BY m.score DESC, m.created_at DESC
    """
    cursor.execute(sql, (user_id, email_clean, user_id, email_clean))
    matches = cursor.fetchall()
    conn.close()
    return matches

