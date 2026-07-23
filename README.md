# CH4PP!3 — Local Business Finder & Outreach

Find local businesses on Google Maps, enrich them, build site concepts in Figma Make, and send outreach from Gmail.

## Live app

**Production:** [https://chappie-production.up.railway.app](https://chappie-production.up.railway.app)

Source: [IS-studio-hub/Chappie](https://github.com/IS-studio-hub/Chappie)

## What it does

1. **Geocodes** an address and searches Google Places in a radius
2. **Filters / enriches** listings (website status, emails, reviews, brand book)
3. **Create Site** — opens a Figma Make brief with logo + brand system
4. **Send Email** — personalized outreach with prototype link + tracking
5. **Pipeline / Favorites** — track outreach and save leads

## Prerequisites

- Python 3.12+ (3.14 is not fully supported by all dependencies)
- Google Cloud credentials with **Places API (New)** + **Geocoding API**
- MongoDB Atlas (or any MongoDB)
- Optional: Stripe, Gmail OAuth, OpenAI, Figma token (connectable in the app UI)

## Local setup

```bash
git clone https://github.com/IS-studio-hub/Chappie.git
cd Chappie

python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium

cp .env.example .env
# Edit .env — set MongoDB, SECRET_KEY, Google credentials, Stripe, etc.
```

Run:

```bash
source venv/bin/activate
python run.py
# or: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Open **http://localhost:8000**

## Environment variables

See [`.env.example`](.env.example). Important:

| Variable | Purpose |
|----------|---------|
| `MONGODB_URI` | Database connection |
| `SECRET_KEY` | Session signing |
| `APP_BASE_URL` | Public URL (local or Railway) |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to service-account JSON (local) |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Full JSON string (Railway / Docker) |
| `GOOGLE_OAUTH_CLIENT_*` | Gmail OAuth |
| `GMAIL_OAUTH_REDIRECT_URI` | Must match Google Cloud Console |
| `STRIPE_*` | Billing |

Never commit `.env`, `*.json` credentials, or `.gmail_token.json`.

## Railway deploy

This repo includes a `Dockerfile` + `railway.toml`.

1. Create a Railway project linked to this repo (or use `railway up`)
2. Set the same env vars as `.env`, using `GOOGLE_SERVICE_ACCOUNT_JSON` instead of a credentials file
3. Generate a Railway domain, then set:
   - `APP_BASE_URL=https://your-app.up.railway.app`
   - `GMAIL_OAUTH_REDIRECT_URI=https://your-app.up.railway.app/api/gmail/oauth/callback`
4. Add that redirect URI in Google Cloud → OAuth client
5. Update Stripe webhook URL to `https://your-app.up.railway.app/api/billing/webhook` (if used)

## Usage

1. Sign up / sign in
2. Connect Gmail, Figma, and OpenAI under **Integrations**
3. Fill **Your Business** (name, info, prototype link)
4. Search for leads → Create Site → Send Email
5. Track in **Pipeline**; star leads in **Favorites**

## API (selected)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/search` | Start a search job |
| `GET` | `/api/search/{job_id}` | Poll job status |
| `POST` | `/api/email/send` | Send outreach |
| `GET` | `/api/pipeline` | Outreach deals |
| `GET` | `/api/favorites` | Saved businesses |
| `GET` | `/api/health` | Health check |

## License

Private/commercial use by IS Studio unless otherwise noted.
