# SmartFind — Campus Lost & Found

SmartFind is a Streamlit browser app for submitting lost/found reports and ranking possible matches. The trained YOLO model in `models/best.pt` detects supported item categories and returns a bounding box; the app analyzes that crop for a color estimate and optional OCR text. Users confirm or edit all suggestions before saving.

## Run locally

Use Python 3.10–3.12. Install the Python packages, then start the Streamlit server:

```powershell
python -m pip install -r requirements.txt
streamlit run main.py
```

Open the local URL printed by Streamlit (normally `http://localhost:8501`) in a browser. Keep `models/best.pt` at its current path, or set `SMARTFIND_MODEL_PATH` to another model file. `SMARTFIND_DETECTION_CONFIDENCE` optionally changes the detector threshold (default `0.40`; tune against representative campus photos).

## Sign-in and roles

SmartFind uses Streamlit's Google OIDC sign-in. Authentication identifies the person; SmartFind then grants the `student` role only to verified emails in `student_email_domains`, and the `admin` role only to exact addresses in `admin_emails`. Admin addresses must also be verified. Students see found reports and their own lost reports; admins can manage all records. Reports use the signed-in email, never a user-entered contact address.

Create `.streamlit/secrets.toml` for local use. This file is git-ignored; never commit real credentials. Register both the local and deployed callback URLs in the Google OAuth client. In Streamlit Community Cloud, paste the same settings into the app's **Secrets** settings, changing `redirect_uri` to the deployed URL:

```toml
[auth]
redirect_uri = "http://localhost:8501/oauth2callback"
cookie_secret = "replace-with-a-long-random-secret"
client_id = "your-google-oauth-client-id"
client_secret = "your-google-oauth-client-secret"
server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"

[smartfind]
student_email_domains = ["tip.edu.ph"]
admin_emails = ["your-admin-account@tip.edu.ph"]
```

For the hosted app, set `redirect_uri` to `https://<your-app-subdomain>.streamlit.app/oauth2callback` and add that exact callback URL to the Google OAuth client's authorized redirect URIs. Add every authorized admin's exact email to `admin_emails`. If your campus uses another identity provider/domain, configure its OIDC metadata URL and email domain instead. Until these secrets are present, the app intentionally blocks access.

### OCR setup

The app uses RapidOCR with ONNX Runtime as its primary OCR engine; install it with `requirements.txt`. It does not require a separate Tesseract executable. Tesseract remains an optional fallback if RapidOCR is unavailable. OCR reads visible text, not logos, and brand candidates must be confirmed by the user.

## Browser deployment

The included `Dockerfile` builds a browser-accessible container. Build and run it with Docker, publishing port `8501`; mount a persistent host directory at `/app/data` so SQLite reports survive container replacement. For Streamlit Community Cloud, deploy `main.py` and retain `models/best.pt` in the repository. That platform's local filesystem may be ephemeral; use persistent storage before relying on it for real reports.

Set `SMARTFIND_DB_PATH` to a writable persistent SQLite path when deploying. Streamlit Community Cloud local storage may be ephemeral; for durable public use, move reports and inbox notifications to managed persistent storage. Uploaded photos are stored as thumbnails in the report database.

## How suggestions and matches work

1. A user uploads or takes a photo in the lost/found form.
2. If YOLO detects an item, the detected crop is analyzed. If it returns no box, SmartFind still runs OCR on the full photo and estimates color from its center region; the category must then be selected manually.
3. RapidOCR reads text and matches it to canonical brand labels, including configured legal-name aliases. The brand form is a category-specific list with OCR candidates, an Unknown choice, and a manual Other option. Color is a rough pixel estimate.
4. Confirmed category, color, canonical brand, date, location, description, owner identity, and photo thumbnail are saved in SQLite.
5. The matching agent compares open Lost and Found reports. Brand shares the existing 10-point description allocation when either report supplies a brand (5 points each); otherwise legacy scoring is unchanged. Strong possible matches create persistent, per-owner inbox notifications; each owner can mark them read. No email is sent. Scores are rule-based similarity, not probabilities or proof of ownership.

YOLO category detection is not instance-level image matching: two objects of the same category are not visually proven to be the same object. The trained model's embedded class names are used to populate the category choices. Color estimation is a basic pixel heuristic and can be affected by lighting/backgrounds; in no-detection fallback it samples the image center. OCR may miss stylized text and does not recognize logo graphics. With no detected category, OCR checks the whole photo against the combined brand shortlist, which may increase false positives; the user must confirm any suggested brand.

## Data, tests, and current limitations

- Reports are stored in `data/smartfind.db`; demo records seed the database only on its first empty launch.
- Run the pure-logic and storage/vision tests with `python -m unittest -v`.
- Notifications are persistent in-app inbox records; no email is sent.
- Use authentic photos from the supported classes to evaluate model accuracy and tune the confidence threshold before operational use.
- Public production use still needs reviewed privacy/retention policies, durable managed storage, backups, and an operational admin process. OIDC identity and role checks are implemented, but must be configured correctly before opening access.
