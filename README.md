# SmartFind — Campus Lost & Found

SmartFind is a Streamlit browser app for submitting lost/found reports and ranking possible matches. The trained YOLO model in `models/best.pt` detects supported item categories and returns a bounding box; the app analyzes that crop for a color estimate and optional OCR text. Users confirm or edit all suggestions before saving.

## Run locally

Use Python 3.10–3.12. Install the Python packages, then start the Streamlit server:

```powershell
python -m pip install -r requirements.txt
streamlit run main.py
```

Open the local URL printed by Streamlit (normally `http://localhost:8501`) in a browser. Keep `models/best.pt` at its current path, or set `SMARTFIND_MODEL_PATH` to another model file. `SMARTFIND_DETECTION_CONFIDENCE` optionally changes the detector threshold (default `0.40`; tune against representative campus photos).

### OCR setup

The app uses RapidOCR with ONNX Runtime as its primary OCR engine; install it with `requirements.txt`. It does not require a separate Tesseract executable. Tesseract remains an optional fallback if RapidOCR is unavailable. OCR reads visible text, not logos, and brand candidates must be confirmed by the user.

## Browser deployment

The included `Dockerfile` builds a browser-accessible container. Build and run it with Docker, publishing port `8501`; mount a persistent host directory at `/app/data` so SQLite reports survive container replacement. For Streamlit Community Cloud, deploy `main.py` and retain `models/best.pt` in the repository. That platform's local filesystem may be ephemeral; use persistent storage before relying on it for real reports.

Set `SMARTFIND_DB_PATH` to a writable persistent SQLite path when deploying. Do not expose the current Admin Management page to the public internet: it has no authentication or role checks and can edit/delete every report. Use a trusted private network or place the app behind an authenticated access proxy until proper user/admin authentication is implemented. Uploaded photos are stored as thumbnails in the report database.

## How suggestions and matches work

1. A user uploads or takes a photo in the lost/found form.
2. If YOLO detects an item, the detected crop is analyzed. If it returns no box, SmartFind still runs OCR on the full photo and estimates color from its center region; the category must then be selected manually.
3. If Tesseract is installed, OCR text is compared with a curated shortlist of up to five common brands for each of the model's 22 categories (or the combined shortlist if the category is unknown). A brand is suggested only when readable text resembles its name. These are practical shortlist examples, not measured market rankings; confirm or correct the result. Color remains a rough pixel estimate.
4. Confirmed category, color, brand text, date, location, description, and photo thumbnail are saved in SQLite.
5. The matching agent compares open Lost and Found reports. Brand shares the existing 10-point description allocation when either report supplies a brand (5 points each); otherwise legacy scoring is unchanged. Scores are rule-based similarity, not probabilities or proof of ownership.

YOLO category detection is not instance-level image matching: two objects of the same category are not visually proven to be the same object. The trained model's embedded class names are used to populate the category choices. Color estimation is a basic pixel heuristic and can be affected by lighting/backgrounds; in no-detection fallback it samples the image center. OCR may miss stylized text and does not recognize logo graphics. With no detected category, OCR checks the whole photo against the combined brand shortlist, which may increase false positives; the user must confirm any suggested brand.

## Data, tests, and current limitations

- Reports are stored in `data/smartfind.db`; demo records seed the database only on its first empty launch.
- Run the pure-logic and storage/vision tests with `python -m unittest -v`.
- Notifications remain simulated in-app records; no email is sent.
- Use authentic photos from the supported classes to evaluate model accuracy and tune the confidence threshold before operational use.
- Public production use still needs authentication, authorization, privacy/retention policy, and managed persistent storage.
