<<<<<<< HEAD
# Aghrinova_ExitCode1
=======
# 🛰️ Landroid Backend

**Land Intelligence Platform** — geo-spatial analysis, satellite imagery, and parcel insights powered by [Supabase](https://supabase.com) and [Microsoft Planetary Computer](https://planetarycomputer.microsoft.com/).

---

## ⚡ Quick Start

### 1. Clone & enter the repo
```bash
git clone <repo-url>
cd landroid-backend
```

### 2. Create a virtual environment
```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure environment variables
```bash
cp .env.example .env
# Edit .env and fill in your real credentials
```

### 5. Run the development server
```bash
uvicorn app.main:app --reload
```

The API will be available at **http://localhost:8000**.  
Interactive docs: **http://localhost:8000/docs**

---

## 📁 Project Structure

```
landroid-backend/
├── app/
│   ├── main.py               # FastAPI app factory & router registration
│   ├── config.py             # Settings loaded from environment variables
│   ├── routes/
│   │   └── health.py         # GET /health
│   ├── models/
│   │   └── common.py         # Shared Pydantic schemas
│   ├── services/
│   │   └── supabase_client.py  # Lazy Supabase client singleton
│   └── utils/
│       └── http.py           # Async httpx client context manager
├── requirements.txt
├── .env                      # Local secrets (git-ignored)
├── .env.example              # Committed template — no real secrets
└── .gitignore
```

---

## 🔑 Environment Variables

| Variable | Required | Description |
|---|---|---|
| `SUPABASE_URL` | ✅ | Your Supabase project URL |
| `SUPABASE_KEY` | ✅ | Supabase anon or service-role key |
| `PLANETARY_COMPUTER_API_KEY` | ⬜ | Microsoft Planetary Computer API key |
| `APP_NAME` | ⬜ | Defaults to `Landroid` |
| `APP_VERSION` | ⬜ | Defaults to `0.1.0` |
| `DEBUG` | ⬜ | Set to `true` for verbose logging |

> **Never commit `.env` to version control.** It is already in `.gitignore`.

---

## 📡 Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Service health check |
| `GET` | `/docs` | Swagger UI |
| `GET` | `/redoc` | ReDoc UI |

---

## 🛠️ Adding a New Route

1. Create `app/routes/your_feature.py` with an `APIRouter`.
2. Register it in `app/main.py`:
   ```python
   from app.routes import your_feature
   app.include_router(your_feature.router, prefix="/your-feature")
   ```
>>>>>>> 60f863e (initial commit: landroid backend with all endpoints working)
