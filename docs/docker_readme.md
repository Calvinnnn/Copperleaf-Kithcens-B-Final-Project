# Copperleaf Kitchens — Docker Setup

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and **running** (open the Docker Desktop GUI and verify the engine is green/started)
- [Docker Compose](https://docs.docker.com/compose/) (included with Docker Desktop)
- A valid **Mistral API key** (required by the backend agent)

---

## Configuration

Copy the environment template and fill in your secrets:

```bash
# Windows
copy .env.example .env

# Mac / Linux
cp .env.example .env
```

Open `.env` and set at minimum:

```env
MISTRAL_API_KEY=your_mistral_api_key_here
```

> **Never commit your `.env` file.** It is already listed in `.gitignore`.

---

## Build

```bash
docker compose build
```

---

## Run

```bash
docker compose up
```

---

## Run detached (background)

```bash
docker compose up -d
```

---

## Stop

```bash
docker compose down
```

---

## Logs

```bash
docker compose logs -f
```

Follow a single service:

```bash
docker compose logs -f backend
```

---

## Rebuild from scratch

```bash
docker compose up --build
```

---

## Reset persistent data

> [!CAUTION]
> The command below **permanently deletes** the HuggingFace model cache volume. Re-downloading embeddings models will be required on next startup.

```bash
docker compose down -v
```

---

## Accessing the application

| Service | URL |
|---|---|
| **User Frontend** (React chat UI) | http://localhost:5173 |
| **Admin Frontend** (Admin dashboard) | http://localhost:5174 |
| **Backend API** (Starlette + MCP) | http://localhost:8000 |
| **Backend Health** | http://localhost:8000/health |
| **Backend MCP endpoint** | http://localhost:8000/mcp |

---

## Architecture Overview

```
                    ┌──────────────────────┐
                    │   User Frontend       │  :5173 (React/Nginx)
                    └─────────┬────────────┘
                              │
                    ┌─────────▼────────────┐
                    │   Admin Frontend      │  :5174 (React/Nginx)
                    └─────────┬────────────┘
                              │
                    ┌─────────▼────────────┐
                    │  Backend (Starlette)  │  :8000
                    │  + MCP Server        │
                    │  + Planning Agent    │
                    │  + Memory/RAG Agent  │
                    └──┬──────────┬────────┘
                       │          │
         ┌─────────────▼──┐  ┌───▼──────────────┐
         │  SQLite DB      │  │  ChromaDB (embed) │
         │  db/copperleaf  │  │  rag/chroma_db    │
         │  .db            │  │  (local files)    │
         └─────────────────┘  └──────────────────┘
```

---

## Database Initialization

The backend container automatically checks for `db/copperleaf.db` on startup.  
If it does not exist, `mcp_server/init_db.py` is run automatically, which applies:

1. `db/schema.sql` — creates all tables
2. `db/seed.sql` — inserts demo data
3. `db/migrate_memory.sql` — episodic/semantic memory tables
4. `db/migrate_checkpoint.sql` — HITL checkpoint tables
5. `db/migrate_admin.sql` — admin registry tables

The `db/` directory is **bind-mounted** so the database persists across container restarts.

---

## RAG / ChromaDB Persistence

ChromaDB is used in **embedded mode** (no separate server).  
The `rag/chroma_db/` and `rag/documents/` directories are **bind-mounted** into the backend container, meaning:

- Existing indexed documents survive container restarts
- New documents uploaded via the Admin Frontend are stored in `rag/documents/` on the host
- They are immediately indexed into `rag/chroma_db/` (same host directory)

To rebuild the RAG index from scratch:

```bash
docker compose exec backend python -m rag.vector_store
```

---

## Docker Image Names

| Image | Description |
|---|---|
| `copperleaf-kithcens-b-final-project-backend` | Python backend / MCP server |
| `copperleaf-kithcens-b-final-project-user-frontend` | User React frontend (Nginx) |
| `copperleaf-kithcens-b-final-project-admin-frontend` | Admin React frontend (Nginx) |

---

## Environment Variables Reference

| Variable | Required | Description |
|---|---|---|
| `MISTRAL_API_KEY` | **Yes** | Mistral AI API key for agent LLM calls |
| `OPENAI_API_KEY` | Optional | OpenAI key (not currently used by backend) |
| `HF_TOKEN` | Optional | HuggingFace token to avoid rate limits when downloading sentence-transformer embeddings |
