# EnterpriseRAGSystem — Project Context

## Tech Stack

- **Backend**: Python 3.11+, FastAPI, SQLAlchemy 2.0, Pydantic v2
- **Vector store**: ChromaDB (dev/default) or PostgreSQL + pgvector (prod)
- **LLM**: DashScope (通义千问) / 智谱 GLM / mock (offline dev)
- **Auth**: bcrypt + JWT (python-jose, HS256)
- **Testing**: pytest, httpx, TestClient

## Architecture

```
app/
├── main.py          # FastAPI entry, lifespan, exception handlers
├── config.py        # pydantic-settings from .env
├── core/
│   └── models.py    # SQLAlchemy models: User, KnowledgeBase, Document, Chunk
├── storage/
│   ├── db.py        # init_db, get_db session context manager
│   └── vector_store.py  # Vector store abstraction (ChromaDB / pgvector)
├── api/
│   ├── auth.py      # /api/auth/* (register, login, me, logout)
│   └── deps.py      # get_db, get_current_user dependencies
└── ...
```

## Development Conventions

### Project workflow (积木式开发)
1. Write a requirement card in `docs/requirements/<id>-<name>.md`
2. Implement to acceptance criteria
3. Run tests: `python -m pytest tests/ -v --tb=short`
4. Update `docs/progress-log.md` and `docs/roadmap.md`
5. Explanation in Chinese, code/commands/paths in English

### API conventions
- Prefix: `/api/...`
- Auth routes: `/api/auth/...`
- Use `app/api/deps.py` for shared dependencies (`get_db`, `get_current_user`)
- Unified JSON errors via `app.main.unhandled_exception` handler
- Response models use Pydantic `ConfigDict(from_attributes=True)` for ORM objects

### Database
- Dev: SQLite (auto-created in `data/`)
- Prod: PostgreSQL 17 + pgvector (Docker: `pgvector/pgvector:pg17`)
- User table: `users` (id, username, hashed_password, created_at)
- Password hashing: bcrypt, truncate to 72 bytes before hashing

### Testing
- Auth tests (B2) require PostgreSQL — auto-skip if unreachable
- Health tests (B1) run offline (no DB needed)
- Use `TestClient(app, raise_server_exceptions=False)` for all API tests
- Clean up test data with raw psycopg2 (avoids SQLAlchemy immutabledict issues on Python 3.13)

### Environment
- Copy `.env.example` to `.env` and fill in secrets
- Default `RAG_PROVIDER=mock` for offline development
- Windows: Docker Desktop at `C:\Users\Strawberry\AppData\Local\Programs\DockerDesktop\`

## Current State

- Stage 1 (A1–A9): Core engine ✅
- A10: pgvector storage switch ✅
- B1: FastAPI skeleton ✅
- B2: Auth (register/login/JWT/me/logout) ✅ — **24 tests green**
- Next: B3 — Knowledge base CRUD (`POST/GET/DELETE /api/kbs`)
