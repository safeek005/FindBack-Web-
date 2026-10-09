import sqlite3
import os
from datetime import datetime
from werkzeug.security import generate_password_hash

DATABASE_PATH = os.path.join(os.path.dirname(__file__), 'findback.db')

def get_db():
    """Returns a SQLite connection with Row factory enabled."""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    """Initializes the database schema if tables do not exist."""
    conn = get_db()
    cursor = conn.cursor()

    cursor.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'user' CHECK(role IN ('user', 'reviewer')),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        type TEXT NOT NULL CHECK(type IN ('lost', 'found')),
        title TEXT NOT NULL,
        category TEXT NOT NULL,
        description TEXT NOT NULL,
        location TEXT NOT NULL,
        incident_date TEXT NOT NULL,
        contact_name TEXT NOT NULL,
        contact_email TEXT NOT NULL,
        contact_phone TEXT DEFAULT '',
        secret_details TEXT DEFAULT '',
        image_filename TEXT DEFAULT '',
        photo_filename TEXT DEFAULT '',
        status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'claim_pending', 'resolved', 'closed')),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE SET NULL
    );

    CREATE TABLE IF NOT EXISTS claims (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_id INTEGER NOT NULL,
        user_id INTEGER,
        claimer_name TEXT NOT NULL,
        claimer_email TEXT NOT NULL,
        claimer_phone TEXT DEFAULT '',
        proof_description TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'approved', 'rejected', 'more_info_needed')),
        reviewer_notes TEXT DEFAULT '',
        ai_verification_notes TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        reviewed_at TIMESTAMP,
        FOREIGN KEY (item_id) REFERENCES items (id) ON DELETE CASCADE,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE SET NULL
    );

    CREATE TABLE IF NOT EXISTS matches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        lost_item_id INTEGER NOT NULL,
        found_item_id INTEGER NOT NULL,
        score INTEGER NOT NULL,
        confidence TEXT NOT NULL CHECK(confidence IN ('High', 'Medium', 'Low')),
        explanation TEXT NOT NULL,
        signals TEXT DEFAULT '',
        source TEXT NOT NULL DEFAULT 'deterministic',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (lost_item_id) REFERENCES items (id) ON DELETE CASCADE,
        FOREIGN KEY (found_item_id) REFERENCES items (id) ON DELETE CASCADE,
        UNIQUE(lost_item_id, found_item_id)
    );

    CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        lost_item_id INTEGER NOT NULL,
        found_item_id INTEGER NOT NULL,
        recipient_email TEXT NOT NULL,
        recipient_name TEXT DEFAULT '',
        match_score INTEGER NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('sent', 'failed', 'skipped')),
        error_message TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (lost_item_id) REFERENCES items (id) ON DELETE CASCADE,
        FOREIGN KEY (found_item_id) REFERENCES items (id) ON DELETE CASCADE,
        UNIQUE(lost_item_id, found_item_id, recipient_email)
    );
    """)

    # Safe migration: ensure columns exist for older databases
    cursor.execute("PRAGMA table_info(items)")
    existing_item_cols = [c[1] for c in cursor.fetchall()]
    if 'image_filename' not in existing_item_cols:
        cursor.execute("ALTER TABLE items ADD COLUMN image_filename TEXT DEFAULT ''")
    if 'photo_filename' not in existing_item_cols:
        cursor.execute("ALTER TABLE items ADD COLUMN photo_filename TEXT DEFAULT ''")
    if 'user_id' not in existing_item_cols:
        cursor.execute("ALTER TABLE items ADD COLUMN user_id INTEGER REFERENCES users(id)")

    cursor.execute("PRAGMA table_info(claims)")
    existing_claim_cols = [c[1] for c in cursor.fetchall()]
    if 'user_id' not in existing_claim_cols:
        cursor.execute("ALTER TABLE claims ADD COLUMN user_id INTEGER REFERENCES users(id)")

    conn.commit()
    conn.close()

def seed_db():
    """Populates realistic seed data for hackathon demonstrations."""
    conn = get_db()
    cursor = conn.cursor()

    # Seed demo accounts if they don't exist
    demo_users = [
        ('Alex Chen', 'alex.chen@university.edu', generate_password_hash('UserPass123!'), 'user'),
        ('Staff Moderator', 'reviewer@findback.org', generate_password_hash('ReviewerPass123!'), 'reviewer')
    ]
    for name, email, pwd_hash, role in demo_users:
        cursor.execute("SELECT id FROM users WHERE email = ?", (email,))
        row = cursor.fetchone()
        if not row:
            cursor.execute("INSERT INTO users (name, email, password_hash, role) VALUES (?, ?, ?, ?)",
                           (name, email, pwd_hash, role))
    conn.commit()

    # Link Alex Chen's user ID to his reported items and claims
    cursor.execute("SELECT id FROM users WHERE email = 'alex.chen@university.edu'")
    alex_row = cursor.fetchone()
    if alex_row:
        alex_id = alex_row[0]
        cursor.execute("UPDATE items SET user_id = ? WHERE contact_email = 'alex.chen@university.edu' AND (user_id IS NULL OR user_id = 0)", (alex_id,))
        cursor.execute("UPDATE claims SET user_id = ? WHERE claimer_email = 'alex.chen@university.edu' AND (user_id IS NULL OR user_id = 0)", (alex_id,))
        conn.commit()

    # Ensure columns exist before query
    cursor.execute("PRAGMA table_info(items)")
    existing_cols = [c[1] for c in cursor.fetchall()]
    if 'image_filename' not in existing_cols:
        cursor.execute("ALTER TABLE items ADD COLUMN image_filename TEXT DEFAULT ''")
    if 'photo_filename' not in existing_cols:
        cursor.execute("ALTER TABLE items ADD COLUMN photo_filename TEXT DEFAULT ''")
    conn.commit()

    # Backfill mapping for existing items
    image_mappings = [
        ('%iPhone%', 'smartphone.svg'),
        ('%Smartphone%', 'smartphone.svg'),
        ('%Wallet%', 'wallet.svg'),
        ('%Headphones%', 'headphones.svg'),
        ('%Key%', 'keys.svg'),
        ('%Backpack%', 'backpack.svg'),
        ('%Glasses%', 'glasses.svg'),
        ('%Bottle%', 'water-bottle.svg'),
        ('%Hydro Flask%', 'water-bottle.svg'),
        ('%Laptop%', 'laptop.svg'),
        ('%MacBook%', 'laptop.svg'),
        ('%Watch%', 'watch.svg'),
        ('%Notebook%', 'notebook.svg'),
    ]

    # Check if items already exist
    cursor.execute("SELECT COUNT(*) FROM items")
    if cursor.fetchone()[0] > 0:
        # Backfill any existing records with missing images
        for pattern, img in image_mappings:
            cursor.execute("UPDATE items SET image_filename = ? WHERE (image_filename IS NULL OR image_filename = '') AND title LIKE ?", (img, pattern))
        cursor.execute("UPDATE items SET image_filename = 'item-default.svg' WHERE image_filename IS NULL OR image_filename = ''")
        conn.commit()
        conn.close()
        return

    sample_items = [
        # LOST 1
        (
            'lost',
            'Midnight Blue iPhone 14 Pro',
            'Electronics',
            'Lost my iPhone 14 Pro with a matte black OtterBox case. The lock screen has a picture of a golden retriever wearing sunglasses. It was left on a study desk on the 2nd floor.',
            'Central Library, 2nd Floor Study Wing',
            '2026-10-06',
            'Alex Chen',
            'alex.chen@university.edu',
            '555-0143',
            'Slight crack in bottom right corner of the glass screen protector; blue sticker of a paw inside case.',
            'smartphone.svg',
            'open'
        ),
        # FOUND 1 (Matches LOST 1)
        (
            'found',
            'Dark Blue Apple Smartphone in Case',
            'Electronics',
            'Found an iPhone in a heavy-duty dark protective case near study table #14 in the quiet wing. Device is charged and turned on; lock screen shows a dog.',
            'Central Library Study Hall',
            '2026-10-06',
            'Campus Security Desk',
            'security@university.edu',
            '555-0100',
            'Screen has golden retriever wallpaper; emergency medical ID lists first name Alex.',
            'smartphone.svg',
            'open'
        ),
        # LOST 2
        (
            'lost',
            'Brown Leather Fossil Bi-Fold Wallet',
            'Wallets & Bags',
            'Lost vintage dark brown leather Fossil wallet. Contains student card, metro pass, and debit card. Lost between the student union and coffee kiosk.',
            'Student Union Commons / Coffee Plaza',
            '2026-10-07',
            'Maya Rodriguez',
            'maya.r@example.org',
            '555-0199',
            'Initials "M.R." lightly embossed inside; silver subway token in coin pocket.',
            'wallet.svg',
            'open'
        ),
        # FOUND 2 (Matches LOST 2)
        (
            'found',
            'Men\'s Brown Leather Wallet',
            'Wallets & Bags',
            'A brown leather folding wallet found on an outdoor bench near the coffee cart. Handed to the information counter.',
            'Coffee Plaza Courtyard',
            '2026-10-07',
            'Student Union Info Desk',
            'infodesk@example.org',
            '555-0112',
            'Has university ID card and transit card inside.',
            'wallet.svg',
            'open'
        ),
        # LOST 3
        (
            'lost',
            'Sony WH-1000XM5 Silver Noise-Cancelling Headphones',
            'Electronics',
            'Left my over-ear Sony headphones in their hard zippered travel case. Silver/grey color with USB-C cable tucked inside the pouch.',
            'Science Building 3, Lecture Hall B',
            '2026-10-05',
            'David Kim',
            'dkim@techlabs.io',
            '555-0182',
            'Small green scratch on the right hinge; name sticker on case interior.',
            'headphones.svg',
            'open'
        ),
        # FOUND 3 (Matches LOST 3)
        (
            'found',
            'Grey Over-Ear Sony Headphones in Travel Case',
            'Electronics',
            'Found high-end wireless headphones in a grey zippered case left on row 4 desk after the afternoon Physics lecture.',
            'Science Building, Room 204',
            '2026-10-05',
            'Prof. Sarah Jenkins',
            'sjenkins@university.edu',
            '555-0133',
            'Grey case with charging lead inside; serial number ending in 9482.',
            'headphones.svg',
            'open'
        ),
        # LOST 4
        (
            'lost',
            'Keychain with Subaru Key fob and Red Carabiner',
            'Keys',
            'Set of 4 keys on a red anodized climbing carabiner, including a Subaru remote key fob and a miniature brass flashlight.',
            'North Parking Lot G',
            '2026-10-08',
            'Jordan Taylor',
            'jtaylor@mail.net',
            '555-0177',
            'Flashlight has Olight logo; gym barcode tag attached.',
            'keys.svg',
            'open'
        ),
        # FOUND 4
        (
            'found',
            'Car Keys with Red Clip',
            'Keys',
            'Car key ring with electronic fob and red metal clip picked up near the walkway beside parking lot G.',
            'Walkway adjacent to Lot G',
            '2026-10-08',
            'Parking Services',
            'parking@university.edu',
            '555-0120',
            'Subaru logo on car fob; 3 brass door keys.',
            'keys.svg',
            'open'
        ),
        # LOST 5
        (
            'lost',
            'Black North Face Borealis Backpack',
            'Wallets & Bags',
            'Standard black backpack containing spiral notebooks, graph paper, and a purple Hydro Flask water bottle.',
            'Recreation Gym Locker Area',
            '2026-10-04',
            'Elena Rostova',
            'elena.r@campus.edu',
            '555-0164',
            'Hydro Flask has sticker of Yosemite National Park; notebook titled "BIO 301".',
            'backpack.svg',
            'open'
        ),
        # FOUND 5 (Unrelated found item)
        (
            'found',
            'Tortoiseshell Ray-Ban Prescription Glasses',
            'Apparel & Accessories',
            'Pair of brown tortoiseshell eyeglasses found on the lawn near the fountain. Kept in a blue microfiber pouch.',
            'Central Fountain Garden',
            '2026-10-08',
            'Marcus Vance',
            'mvance@example.org',
            '555-0155',
            'Clubmaster style frame; mild progressive lenses.',
            'glasses.svg',
            'open'
        )
    ]

    cursor.executemany("""
    INSERT INTO items (
        type, title, category, description, location, incident_date,
        contact_name, contact_email, contact_phone, secret_details,
        image_filename, status
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, sample_items)

    conn.commit()

    # Add a sample claim for demonstration
    # Item #2 is the found iPhone
    cursor.execute("""
    INSERT INTO claims (
        item_id, claimer_name, claimer_email, claimer_phone,
        proof_description, status, reviewer_notes, ai_verification_notes
    ) VALUES (
        2,
        'Alex Chen',
        'alex.chen@university.edu',
        '555-0143',
        'I lost this phone in the 2nd floor library! The lock screen shows my dog Sparky (a golden retriever in sunglasses). The screen protector has a tiny chip in the lower right corner, and inside the OtterBox case is a blue paw sticker.',
        'pending',
        'Pending physical inspection of the paw sticker inside case.',
        'AI SUGGESTIONS: 1. Ask claimer to describe the phone wallpaper and breed of dog. 2. Verify emergency medical ID contact name. 3. Check for the small paw print sticker inside the OtterBox casing.'
    )
    """)

    conn.commit()
    conn.close()

if __name__ == '__main__':
    init_db()
    seed_db()
    print("Database initialized and seeded successfully.")
