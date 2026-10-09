import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

import models

load_dotenv()

def get_mail_config():
    """Retrieves mail configuration from environment variables."""
    return {
        'server': os.getenv('MAIL_SERVER', 'smtp.gmail.com').strip(),
        'port': int(os.getenv('MAIL_PORT', 587)),
        'use_tls': os.getenv('MAIL_USE_TLS', 'true').lower() in ('true', '1', 'yes'),
        'username': os.getenv('MAIL_USERNAME', '').strip(),
        'password': os.getenv('MAIL_PASSWORD', '').strip(),
        'sender': os.getenv('MAIL_DEFAULT_SENDER', '').strip() or os.getenv('MAIL_USERNAME', '').strip(),
        'threshold': int(os.getenv('MATCH_NOTIFICATION_THRESHOLD', 60)),
    }

def send_match_notification(lost_item, found_item, match_score, base_url="http://127.0.0.1:5000"):
    """
    Evaluates eligibility and sends email notifications for match candidates scoring
    STRICTLY GREATER THAN MATCH_NOTIFICATION_THRESHOLD (e.g. score > 60).
    A score of exactly 60 does not trigger notifications.
    Deduplicates to prevent repeatedly notifying for the same pair.
    """
    config = get_mail_config()
    threshold = config['threshold']

    # Safely handle sqlite3.Row by converting to dict
    lost_item = dict(lost_item) if lost_item else {}
    found_item = dict(found_item) if found_item else {}

    # Must be strictly greater than threshold
    if match_score <= threshold:
        return False

    lost_id = lost_item['id']
    found_id = found_item['id']

    # Target recipients: primarily the lost item owner/reporter
    recipients = []

    # 1. Lost item reporter
    lost_email = (lost_item.get('contact_email') or '').strip()
    lost_name = (lost_item.get('contact_name') or 'User').strip()
    if lost_email:
        recipients.append({
            'role': 'lost_reporter',
            'email': lost_email,
            'name': lost_name,
            'my_report_title': lost_item['title'],
            'matched_report_title': found_item['title'],
            'matched_item_id': found_id,
            'subject': 'A potential match was found for your lost item'
        })

    # 2. Found item reporter (if different email and valid)
    found_email = (found_item.get('contact_email') or '').strip()
    found_name = (found_item.get('contact_name') or 'User').strip()
    if found_email and found_email.lower() != lost_email.lower():
        recipients.append({
            'role': 'found_reporter',
            'email': found_email,
            'name': found_name,
            'my_report_title': found_item['title'],
            'matched_report_title': lost_item['title'],
            'matched_item_id': lost_id,
            'subject': 'A potential match was found for your found item report'
        })

    has_credentials = bool(config['username'] and config['password'])

    for r in recipients:
        email = r['email']
        name = r['name']

        # Check deduplication: avoid repeatedly notifying for the same match pair
        if models.has_notification_been_sent(lost_id, found_id, email):
            continue

        item_url = f"{base_url.rstrip('/')}/items/{r['matched_item_id']}"

        # Construct email message body
        body_text = f"""Hello {name},

FindBack Web has identified a potential match for one of your reports.

Your report:
{r['my_report_title']}

Potential matching report:
{r['matched_report_title']}

Match score:
{match_score}%

The reports share some relevant attributes. Please review the details and verify the item before proceeding.

A potential match is not proof of ownership. Final ownership verification remains part of the claim-review process.

View the relevant report:
{item_url}

Regards,
FindBack Web
"""

        if not has_credentials:
            # Record as skipped due to unconfigured email credentials
            models.record_notification(
                lost_item_id=lost_id,
                found_item_id=found_id,
                recipient_email=email,
                recipient_name=name,
                match_score=match_score,
                status='skipped',
                error_message='Email credentials not configured'
            )
            continue

        try:
            msg = MIMEMultipart()
            msg['From'] = config['sender']
            msg['To'] = email
            msg['Subject'] = r['subject']
            msg.attach(MIMEText(body_text, 'plain', 'utf-8'))

            server = smtplib.SMTP(config['server'], config['port'], timeout=10)
            if config['use_tls']:
                server.starttls()
            server.login(config['username'], config['password'])
            server.sendmail(config['sender'], [email], msg.as_string())
            server.quit()

            models.record_notification(
                lost_item_id=lost_id,
                found_item_id=found_id,
                recipient_email=email,
                recipient_name=name,
                match_score=match_score,
                status='sent',
                error_message=''
            )
        except Exception as e:
            # Do not raise exception or break application flow
            err_msg = str(e)
            models.record_notification(
                lost_item_id=lost_id,
                found_item_id=found_id,
                recipient_email=email,
                recipient_name=name,
                match_score=match_score,
                status='failed',
                error_message=err_msg
            )

    return True
