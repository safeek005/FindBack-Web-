import os
import io
import unittest
from app import app
import db
import models
import ai_service

class FindBackTestCase(unittest.TestCase):
    def setUp(self):
        app.config['TESTING'] = True
        self.client = app.test_client()
        # Clean and reseed database for isolated test execution
        conn = db.get_db()
        cursor = conn.cursor()
        cursor.execute("DROP TABLE IF EXISTS matches")
        cursor.execute("DROP TABLE IF EXISTS claims")
        cursor.execute("DROP TABLE IF EXISTS items")
        conn.commit()
        conn.close()

        with app.app_context():
            db.init_db()
            db.seed_db()

    def test_homepage(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'FindBack', response.data)
        self.assertIn(b'Smart Match Agent', response.data)
        self.assertIn(b'Claim Verification Agent', response.data)

    def test_item_list_and_filters(self):
        # 1. All items
        response = self.client.get('/items')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Community Listings', response.data)

        # 2. Filter lost only
        response = self.client.get('/items?type=lost')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'LOST', response.data)

        # 3. Filter category
        response = self.client.get('/items?category=Electronics')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Electronics', response.data)

        # 4. Keyword search
        response = self.client.get('/items?q=iPhone')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'iPhone', response.data)

    def test_report_item_submission_and_persistence(self):
        # Submit a new lost item
        payload = {
            'type': 'lost',
            'title': 'Blue Hydro Flask 32oz',
            'category': 'Other',
            'description': 'Lost my navy blue Hydro Flask bottle with stickers from Colorado and national parks.',
            'location': 'Student Union Gym 1st Floor',
            'incident_date': '2026-10-08',
            'contact_name': 'Samira Khan',
            'contact_email': 'samira@example.com',
            'contact_phone': '555-9011',
            'secret_details': 'Dent on the bottom rim, sticker of a mountain goat on underside.'
        }
        post_response = self.client.post('/report', data=payload, follow_redirects=True)
        self.assertEqual(post_response.status_code, 200)
        self.assertIn(b'Blue Hydro Flask 32oz', post_response.data)

        # Verify persistence in database
        items = models.get_items(query='Hydro Flask')
        self.assertTrue(len(items) >= 1)
        matching_items = [i for i in items if i['title'] == 'Blue Hydro Flask 32oz']
        self.assertTrue(len(matching_items) > 0)
        item = matching_items[0]
        self.assertEqual(item['title'], 'Blue Hydro Flask 32oz')
        self.assertEqual(item['contact_name'], 'Samira Khan')
        self.assertEqual(item['secret_details'], 'Dent on the bottom rim, sticker of a mountain goat on underside.')

    def test_item_detail_and_smart_match(self):
        response = self.client.get('/items/1')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Midnight Blue iPhone 14 Pro', response.data)
        self.assertIn(b'Smart Match Agent', response.data)

        # Trigger smart match scan
        post_match = self.client.post('/items/1/match', follow_redirects=True)
        self.assertEqual(post_match.status_code, 200)
        self.assertIn(b'Smart Match Agent scan complete', post_match.data)

    def test_claim_submission_and_reviewer_flow(self):
        # Found item #2 (Dark Blue Smartphone)
        claim_payload = {
            'claimer_name': 'Alex Taylor',
            'claimer_email': 'alex.taylor@example.com',
            'claimer_phone': '555-7788',
            'proof_description': 'This is my phone. The lock screen has a photo of my golden retriever. There is a blue paw sticker inside the OtterBox case.'
        }
        response = self.client.post('/items/2/claim', data=claim_payload, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'claim has been submitted', response.data)

        # Check in reviewer dashboard
        rev_resp = self.client.get('/reviewer')
        self.assertEqual(rev_resp.status_code, 200)
        self.assertIn(b'Alex Taylor', rev_resp.data)

        # Inspect claim detail
        claims = models.get_claims()
        target_claim = [c for c in claims if c['claimer_email'] == 'alex.taylor@example.com'][0]

        detail_resp = self.client.get(f'/reviewer/claims/{target_claim["id"]}')
        self.assertEqual(detail_resp.status_code, 200)
        self.assertIn(b'Claim Inspection & Verification', detail_resp.data)
        self.assertIn(b'Claim Verification Agent', detail_resp.data)
        self.assertIn(b'HUMAN REVIEW MANDATORY', detail_resp.data)

        # Reviewer approves claim
        decision_payload = {
            'decision': 'approved',
            'reviewer_notes': 'Claimant successfully identified golden retriever photo and showed matching ID.'
        }
        dec_resp = self.client.post(f'/reviewer/claims/{target_claim["id"]}/decision', data=decision_payload, follow_redirects=True)
        self.assertEqual(dec_resp.status_code, 200)
        self.assertIn(b'Claim approved', dec_resp.data)

        # Check item status is now resolved
        item = models.get_item_by_id(2)
        self.assertEqual(item['status'], 'resolved')

    def test_privacy_guarantee_for_secret_and_claims(self):
        # Item #1 has secret details in DB: "Slight crack in bottom right corner..."
        # Verify secret text is NOT displayed on the public item detail page
        detail_resp = self.client.get('/items/1')
        self.assertNotIn(b'Slight crack in bottom right corner', detail_resp.data)
        self.assertIn(b'Private Verification Details on File', detail_resp.data)

        # Verify public listing page doesn't show secret details or claims
        list_resp = self.client.get('/items')
        self.assertNotIn(b'Slight crack in bottom right corner', list_resp.data)
        self.assertNotIn(b'alex.taylor@example.com', list_resp.data)

    def test_claim_never_autonomously_approves(self):
        # When a claim is submitted, verify claim remains 'pending' and item remains 'claim_pending'
        claim_payload = {
            'claimer_name': 'Pending Claimant',
            'claimer_email': 'pending@example.com',
            'claimer_phone': '555-1234',
            'proof_description': 'Here is detailed proof of my item that I lost.'
        }
        self.client.post('/items/2/claim', data=claim_payload, follow_redirects=True)

        claims = models.get_claims()
        target = [c for c in claims if c['claimer_email'] == 'pending@example.com'][0]
        self.assertEqual(target['status'], 'pending')

        item = models.get_item_by_id(2)
        self.assertNotEqual(item['status'], 'resolved')
        self.assertEqual(item['status'], 'claim_pending')

    def test_deterministic_fallback_when_offline(self):
        # Force offline/no-key mode
        orig = os.environ.get('GEMINI_API_KEY')
        os.environ['GEMINI_API_KEY'] = ''
        try:
            lost = {'title': 'Sony WH-1000XM5', 'category': 'Electronics', 'incident_date': '2026-10-05', 'location': 'Science Building', 'description': 'Silver noise cancelling headphones in case'}
            found = {'title': 'Sony Headphones', 'category': 'Electronics', 'incident_date': '2026-10-05', 'location': 'Science Building Room 204', 'description': 'High-end headphones in grey zippered case'}
            match = ai_service.smart_match_agent(lost, found)
            self.assertEqual(match['source'], 'deterministic')
            self.assertTrue(match['score'] >= 50)
            self.assertIn(match['confidence'], ['High', 'Medium', 'Low'])

            verification = ai_service.claim_verification_agent(found, {'claimer_name': 'Test', 'proof_description': 'Has case'})
            self.assertEqual(verification['source'], 'deterministic')
            self.assertTrue(len(verification['verification_questions']) >= 2)
            self.assertIn('HUMAN REVIEW MANDATORY', verification['policy_notice'])
        finally:
            if orig:
                os.environ['GEMINI_API_KEY'] = orig

    def test_server_validation_errors(self):
        # 1. Invalid report submission (short title, missing fields)
        bad_report = {
            'type': 'lost',
            'title': 'X',
            'category': 'Electronics',
            'description': 'short',
            'location': '',
            'incident_date': '2099-01-01', # future date
            'contact_name': '',
            'contact_email': 'not-an-email'
        }
        resp = self.client.post('/report', data=bad_report)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Item title must be at least 3 characters', resp.data)
        self.assertIn(b'minimum 10 characters', resp.data)
        self.assertIn(b'Incident date cannot be in the future', resp.data)

        # 2. Invalid claim submission (short proof)
        bad_claim = {
            'claimer_name': 'A',
            'claimer_email': 'invalid',
            'proof_description': 'too short'
        }
        claim_resp = self.client.post('/items/2/claim', data=bad_claim)
        self.assertEqual(claim_resp.status_code, 200)
        self.assertIn(b'Please describe your proof of ownership in detail', claim_resp.data)

    def test_no_javascript_in_rendered_templates(self):
        # Inspect rendered output of all public pages
        pages = ['/', '/items', '/items/1', '/report', '/matches', '/reviewer']
        for page in pages:
            resp = self.client.get(page)
            self.assertEqual(resp.status_code, 200)
            html = resp.data.lower()
            self.assertNotIn(b'<script', html, f"JavaScript found in {page}")
            self.assertNotIn(b'javascript:', html, f"JavaScript URI found in {page}")
            self.assertNotIn(b'onclick=', html, f"Inline JS handler found in {page}")

    def test_smart_match_hub(self):
        response = self.client.get('/matches?lost_id=1&found_id=2')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Match Result', response.data)
        self.assertIn(b'Agent Explanation', response.data)

    def test_product_image_rendering_and_fallbacks(self):
        # 1. Homepage renders product images with illustrative badges
        home_resp = self.client.get('/')
        self.assertEqual(home_resp.status_code, 200)
        self.assertIn(b'item-card-image', home_resp.data)
        self.assertIn(b'Illustrative', home_resp.data)
        self.assertIn(b'alt="Illustrative image for', home_resp.data)

        # 2. Browse listings page renders product images
        list_resp = self.client.get('/items')
        self.assertEqual(list_resp.status_code, 200)
        self.assertIn(b'item-card-image', list_resp.data)
        self.assertIn(b'smartphone.svg', list_resp.data)
        self.assertIn(b'wallet.svg', list_resp.data)

        # 3. Item detail page renders detail image and illustrative disclaimer
        detail_resp = self.client.get('/items/1')
        self.assertEqual(detail_resp.status_code, 200)
        self.assertIn(b'item-detail-image', detail_resp.data)
        self.assertIn(b'smartphone.svg', detail_resp.data)
        self.assertIn(b'Illustrative Reference', detail_resp.data)
        self.assertIn(b'Note: This image is an illustrative category reference', detail_resp.data)

        # 4. Fallback works when image_filename is empty
        fallback_item = {'title': 'Vintage Mystery Antique', 'category': 'Other', 'image_filename': ''}
        img_file = models.get_default_image_for_item(fallback_item['title'], fallback_item['category'])
        self.assertEqual(img_file, 'item-default.svg')

        # 5. Image inference from title works
        self.assertEqual(models.get_default_image_for_item('Lost Black Backpack', 'Wallets & Bags'), 'backpack.svg')
        self.assertEqual(models.get_default_image_for_item('Apple MacBook Air', 'Electronics'), 'laptop.svg')
        self.assertEqual(models.get_default_image_for_item('Silver Wrist Watch', 'Jewelry & Watches'), 'watch.svg')
        self.assertEqual(models.get_default_image_for_item('Chemistry Notebook', 'Books & Notebooks'), 'notebook.svg')

    def test_report_with_valid_image_upload(self):
        # Create a valid minimal JPEG payload (FF D8 FF E0 ...)
        valid_jpeg = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00' + b'\x00' * 50
        data = {
            'type': 'lost',
            'title': 'Found Purple Sunglasses',
            'category': 'Apparel & Accessories',
            'description': 'Found in the quad on the picnic bench near oak tree.',
            'location': 'Main Quad Picnic Area',
            'incident_date': '2026-10-09',
            'contact_name': 'Taylor Swift',
            'contact_email': 'taylor@example.com',
            'photo': (io.BytesIO(valid_jpeg), 'my_photo.jpg')
        }
        resp = self.client.post('/report', data=data, content_type='multipart/form-data', follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Found Purple Sunglasses', resp.data)
        self.assertIn(b'>Item Photo</span>', resp.data)
        self.assertIn(b'photo-tag', resp.data)
        self.assertIn(b'Photograph uploaded by reporter', resp.data)
        self.assertIn(b'/static/uploads/photo_', resp.data)

        # Check DB persistence
        items = models.get_items(query='Purple Sunglasses')
        self.assertTrue(len(items) > 0)
        item = items[0]
        self.assertTrue(item['photo_filename'].startswith('photo_'))
        self.assertTrue(item['photo_filename'].endswith('.jpg'))

        # Check file exists on disk
        upload_path = os.path.join(app.root_path, 'static', 'uploads', item['photo_filename'])
        self.assertTrue(os.path.isfile(upload_path))

        # Cleanup
        if os.path.exists(upload_path):
            os.remove(upload_path)

    def test_report_with_valid_png_and_webp_upload(self):
        # 1. Valid PNG
        valid_png = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR' + b'\x00' * 30
        data_png = {
            'type': 'found',
            'title': 'Silver Car Keys with Ring',
            'category': 'Keys',
            'description': 'Found near lecture hall B entrance on the brick pathway.',
            'location': 'Hall B Pathway',
            'incident_date': '2026-10-09',
            'contact_name': 'Morgan Lee',
            'contact_email': 'morgan@example.com',
            'photo': (io.BytesIO(valid_png), 'keys.png')
        }
        resp_png = self.client.post('/report', data=data_png, content_type='multipart/form-data', follow_redirects=True)
        self.assertEqual(resp_png.status_code, 200)
        items_png = models.get_items(query='Silver Car Keys')
        self.assertTrue(len(items_png) > 0)
        png_filename = items_png[0]['photo_filename']
        self.assertTrue(png_filename.endswith('.png'))
        png_path = os.path.join(app.root_path, 'static', 'uploads', png_filename)
        self.assertTrue(os.path.isfile(png_path))
        if os.path.exists(png_path):
            os.remove(png_path)

        # 2. Valid WebP
        valid_webp = b'RIFF\x24\x00\x00\x00WEBPVP8 \x18\x00\x00\x00' + b'\x00' * 30
        data_webp = {
            'type': 'lost',
            'title': 'Green Canvas Tote Bag',
            'category': 'Wallets & Bags',
            'description': 'Left on bench outside cafeteria with books inside.',
            'location': 'Cafeteria Patio Bench',
            'incident_date': '2026-10-09',
            'contact_name': 'Dana Scully',
            'contact_email': 'dana@example.com',
            'photo': (io.BytesIO(valid_webp), 'tote.webp')
        }
        resp_webp = self.client.post('/report', data=data_webp, content_type='multipart/form-data', follow_redirects=True)
        self.assertEqual(resp_webp.status_code, 200)
        items_webp = models.get_items(query='Green Canvas Tote')
        self.assertTrue(len(items_webp) > 0)
        webp_filename = items_webp[0]['photo_filename']
        self.assertTrue(webp_filename.endswith('.webp'))
        webp_path = os.path.join(app.root_path, 'static', 'uploads', webp_filename)
        self.assertTrue(os.path.isfile(webp_path))
        if os.path.exists(webp_path):
            os.remove(webp_path)

    def test_report_without_image_upload(self):
        # Submitting without a photograph must succeed gracefully
        data = {
            'type': 'lost',
            'title': 'Yellow Umbrella with Wooden Handle',
            'category': 'Other',
            'description': 'Left in classroom 104 in the corner umbrella stand.',
            'location': 'Classroom 104',
            'incident_date': '2026-10-08',
            'contact_name': 'Ted Mosby',
            'contact_email': 'ted@example.com',
            'photo': (io.BytesIO(b''), '')
        }
        resp = self.client.post('/report', data=data, content_type='multipart/form-data', follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Yellow Umbrella with Wooden Handle', resp.data)
        self.assertIn(b'Illustrative Reference', resp.data)
        self.assertNotIn(b'photo-tag', resp.data)
        self.assertNotIn(b'>Item Photo</span>', resp.data)

        items = models.get_items(query='Yellow Umbrella')
        self.assertTrue(len(items) > 0)
        self.assertEqual(items[0]['photo_filename'], '')

    def test_report_with_invalid_file_type(self):
        # Fake file disguised with .jpg extension
        evil_content = b'<html><script>alert("xss")</script></html>'
        data = {
            'type': 'lost',
            'title': 'Fake Image Test Item',
            'category': 'Electronics',
            'description': 'Testing rejection of invalid image content masquerading as JPEG.',
            'location': 'Test Lab',
            'incident_date': '2026-10-08',
            'contact_name': 'Hacker Bob',
            'contact_email': 'bob@example.com',
            'photo': (io.BytesIO(evil_content), 'malicious.jpg')
        }
        resp = self.client.post('/report', data=data, content_type='multipart/form-data', follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Invalid image file', resp.data)
        # Verify no item was persisted
        items = models.get_items(query='Fake Image Test')
        self.assertEqual(len(items), 0)

    def test_report_with_oversized_upload(self):
        # Upload exceeding 5 MB limit
        oversized_data = b'\xff\xd8\xff' + b'0' * (5 * 1024 * 1024 + 100)
        data = {
            'type': 'lost',
            'title': 'Oversized Photo Test Item',
            'category': 'Electronics',
            'description': 'Testing server rejection of images larger than 5 megabytes.',
            'location': 'Server Room',
            'incident_date': '2026-10-08',
            'contact_name': 'Heavy Loader',
            'contact_email': 'heavy@example.com',
            'photo': (io.BytesIO(oversized_data), 'huge.jpg')
        }
        resp = self.client.post('/report', data=data, content_type='multipart/form-data', follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'exceeds the 5 MB', resp.data)
        items = models.get_items(query='Oversized Photo Test')
        self.assertEqual(len(items), 0)

    def test_missing_uploaded_image_fallback(self):
        # Create an item in DB whose photo_filename points to a missing file on disk
        item_id = models.create_item({
            'type': 'lost',
            'title': 'Lost Vintage Camera',
            'category': 'Electronics',
            'description': 'Has a leather strap and 35mm lens attached.',
            'location': 'Film Studio',
            'incident_date': '2026-10-07',
            'contact_name': 'Ansel Adams',
            'contact_email': 'ansel@example.com',
            'photo_filename': 'missing_nonexistent_photo.jpg'
        })
        resp = self.client.get(f'/items/{item_id}')
        self.assertEqual(resp.status_code, 200)
        # Missing file on disk must gracefully fall back to illustrative SVG without 500 error
        self.assertIn(b'smartphone.svg', resp.data)
        self.assertIn(b'Illustrative Reference', resp.data)
        self.assertNotIn(b'photo-tag', resp.data)
        self.assertNotIn(b'>Item Photo</span>', resp.data)

if __name__ == '__main__':
    unittest.main()

