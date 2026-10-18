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

The app uses Tesseract OCR through `pytesseract`. Install the Tesseract executable on the host as well as the Python requirements. On Windows, install the Tesseract OCR application and add its installation directory to `PATH`; on Debian/Ubuntu install `tesseract-ocr`. Without the executable, object detection and color estimation still work, and the user can enter the brand manually. OCR reads visible text, not logos, and its text candidate must be confirmed as a brand by the user.

## Browser deployment

The included `Dockerfile` builds a browser-accessible container with Tesseract installed. Build and run it with Docker, publishing port `8501`; mount a persistent host directory at `/app/data` so SQLite reports survive container replacement. For Streamlit Community Cloud, deploy `main.py`, retain `models/best.pt` in the repository, and use `packages.txt` to install Tesseract. That platform's local filesystem may be ephemeral; use persistent storage before relying on it for real reports.

Set `SMARTFIND_DB_PATH` to a writable persistent SQLite path when deploying. Do not expose the current Admin Management page to the public internet: it has no authentication or role checks and can edit/delete every report. Use a trusted private network or place the app behind an authenticated access proxy until proper user/admin authentication is implemented. Uploaded photos are stored as thumbnails in the report database.

## How suggestions and matches work

1. A user uploads or takes a photo in the lost/found form.
2. YOLO suggests the best-scoring trained category and its crop. The user confirms the category.
3. The crop is analyzed for a rough dominant color. If Tesseract is installed, OCR offers a visible-text candidate; neither suggestion is treated as certain.
4. Confirmed category, color, brand text, date, location, description, and photo thumbnail are saved in SQLite.
5. The matching agent compares open Lost and Found reports. Brand shares the existing 10-point description allocation when either report supplies a brand (5 points each); otherwise legacy scoring is unchanged. Scores are rule-based similarity, not probabilities or proof of ownership.

YOLO category detection is not instance-level image matching: two objects of the same category are not visually proven to be the same object. The trained model's embedded class names are used to populate the category choices. Color estimation is a basic pixel heuristic and can be affected by lighting/backgrounds. OCR may miss stylized text and does not recognize logo graphics.

## Data, tests, and current limitations

- Reports are stored in `data/smartfind.db`; demo records seed the database only on its first empty launch.
- Run the pure-logic and storage/vision tests with `python -m unittest -v`.
- Notifications remain simulated in-app records; no email is sent.
- Use authentic photos from the supported classes to evaluate model accuracy and tune the confidence threshold before operational use.
- Public production use still needs authentication, authorization, privacy/retention policy, and managed persistent storage.
