# QA-SQL Multi-Tenant API

A RESTful API platform for Text-to-SQL generation with multi-tenant support. Users can register, create projects with their own databases, and generate SQL queries using the QA-SQL engine.

---

## Features

- **User Authentication**: JWT-based authentication with API key support
- **Multi-Tenant Projects**: Each user can create multiple projects with different databases
- **SQL Generation**: Natural language to SQL using QA-SQL engine
- **Query History**: Track all queries per project
- **LLM Flexibility**: Configure LLM provider per project (Ollama, Anthropic, OpenAI)
- **Project Security**: Project-specific API keys with fine-grained permissions
- **Collaboration**: Invite team members with different roles (viewer, editor, admin)
- **Rate Limiting**: Control API usage per project key

---

## Quick Start

### 1. Install Dependencies

```bash
cd qasql-api
pip install -r requirements.txt
```

### 2. Start the Server

```bash
# Default (uses SQLite for platform database)
python app.py

# With Ollama
python app.py --ollama-url http://localhost:11434

# Production (network accessible)
python app.py --host 0.0.0.0 --port 8000
```

### 3. Access the API

- **Docs**: http://localhost:8000/docs
- **Health**: http://localhost:8000/api/health

---

## API Endpoints

### Authentication

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/auth/register` | Register new user |
| POST | `/api/auth/login` | Login and get JWT token |
| GET | `/api/auth/me` | Get current user info |
| PUT | `/api/auth/me` | Update user profile |

### API Keys

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/auth/api-keys` | Create new API key |
| GET | `/api/auth/api-keys` | List user's API keys |
| DELETE | `/api/auth/api-keys/{key_id}` | Revoke API key |

### Projects

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/projects` | Create new project |
| GET | `/api/projects` | List user's projects |
| GET | `/api/projects/{id}` | Get project details |
| PUT | `/api/projects/{id}` | Update project |
| DELETE | `/api/projects/{id}` | Delete project |
| POST | `/api/projects/{id}/setup` | Extract schema from database |
| GET | `/api/projects/{id}/tables` | List project tables |
| GET | `/api/projects/{id}/schema/{table}` | Get table schema |

### Query

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/projects/{id}/query` | Generate SQL from question |
| POST | `/api/projects/{id}/execute` | Execute SQL query |
| GET | `/api/projects/{id}/history` | Get query history |

### Project Security

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/projects/{id}/api-keys` | Create project API key |
| GET | `/api/projects/{id}/api-keys` | List project API keys |
| DELETE | `/api/projects/{id}/api-keys/{key_id}` | Revoke project API key |
| PUT | `/api/projects/{id}/api-keys/{key_id}/toggle` | Enable/disable API key |
| POST | `/api/projects/{id}/members` | Invite user to project |
| GET | `/api/projects/{id}/members` | List project members |
| PUT | `/api/projects/{id}/members/{member_id}` | Update member role |
| DELETE | `/api/projects/{id}/members/{member_id}` | Remove member |

### Project API Key Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/projects/{id}/query/key` | Query with project key |
| POST | `/api/projects/{id}/execute/key` | Execute with project key |
| GET | `/api/projects/{id}/tables/key` | List tables with project key |

### User Invitations

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/user/invitations` | List pending invitations |
| POST | `/api/user/invitations/{id}/accept` | Accept invitation |
| POST | `/api/user/invitations/{id}/reject` | Reject invitation |

---

## Usage Examples

### 1. Register a User

```bash
curl -X POST http://localhost:8000/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "user@example.com",
    "username": "myuser",
    "password": "secretpass123",
    "full_name": "John Doe"
  }'
```

### 2. Login

```bash
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=myuser&password=secretpass123"
```

Response:
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer",
  "expires_in": 86400
}
```

### 3. Create a Project

```bash
curl -X POST http://localhost:8000/api/projects \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My Sales DB",
    "description": "Sales analytics database",
    "db_type": "sqlite",
    "db_uri": "/path/to/sales.db",
    "llm_provider": "ollama",
    "llm_model": "llama3.2:3b"
  }'
```

### 4. Setup Project (Extract Schema)

```bash
curl -X POST http://localhost:8000/api/projects/1/setup \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### 5. Generate SQL Query

```bash
curl -X POST http://localhost:8000/api/projects/1/query \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "What is the total revenue by product category?"
  }'
```

Response:
```json
{
  "sql": "SELECT category, SUM(revenue) as total_revenue FROM sales GROUP BY category",
  "confidence": 0.85,
  "question": "What is the total revenue by product category?",
  "reasoning": "Aggregating revenue by category using GROUP BY",
  "successful_candidates": 4,
  "total_candidates": 5,
  "execution_time_ms": 2345.67
}
```

### 6. Execute SQL

```bash
curl -X POST http://localhost:8000/api/projects/1/execute \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "sql": "SELECT category, SUM(revenue) FROM sales GROUP BY category"
  }'
```

### 7. Create API Key

```bash
curl -X POST http://localhost:8000/api/auth/api-keys \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Production Key",
    "expires_days": 365
  }'
```

Response:
```json
{
  "id": 1,
  "key": "qasql_abc123...",
  "name": "Production Key",
  "expires_at": "2027-03-08T00:00:00"
}
```

### 8. Use API Key

```bash
curl -X POST http://localhost:8000/api/projects/1/query \
  -H "X-API-Key: qasql_abc123..." \
  -H "Content-Type: application/json" \
  -d '{"question": "How many customers do we have?"}'
```

---

## Project Security

### Project API Keys

Create restricted API keys for external applications or services that need access to specific projects.

#### Create a Project API Key

```bash
curl -X POST http://localhost:8000/api/projects/1/api-keys \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Production Read-Only",
    "can_query": true,
    "can_execute": false,
    "can_view_schema": true,
    "rate_limit_per_hour": 100,
    "expires_days": 90
  }'
```

Response:
```json
{
  "id": 1,
  "name": "Production Read-Only",
  "key": "proj_abc123def456...",
  "can_query": true,
  "can_execute": false,
  "can_view_schema": true,
  "rate_limit_per_hour": 100,
  "expires_at": "2026-06-08T00:00:00"
}
```

#### Use Project API Key

```bash
curl -X POST http://localhost:8000/api/projects/1/query/key \
  -H "X-Project-Key: proj_abc123def456..." \
  -H "Content-Type: application/json" \
  -d '{"question": "How many orders this month?"}'
```

#### Key Permissions

| Permission | Description |
|------------|-------------|
| `can_query` | Generate SQL from natural language |
| `can_execute` | Execute SQL queries on database |
| `can_view_schema` | View tables and column information |

### Project Sharing

Invite team members to collaborate on projects with different access levels.

#### Invite a User

```bash
curl -X POST http://localhost:8000/api/projects/1/members \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "username_or_email": "colleague@example.com",
    "role": "editor"
  }'
```

#### Member Roles

| Role | View | Query | Execute | Manage |
|------|------|-------|---------|--------|
| `viewer` | Yes | No | No | No |
| `editor` | Yes | Yes | Yes | No |
| `admin` | Yes | Yes | Yes | Yes |
| `owner` | Yes | Yes | Yes | Yes |

- **Viewer**: Can view project details, tables, and schema
- **Editor**: Can generate and execute SQL queries
- **Admin**: Full access including managing members and API keys
- **Owner**: Project creator, cannot be removed

#### Accept an Invitation

```bash
# List pending invitations
curl http://localhost:8000/api/user/invitations \
  -H "Authorization: Bearer YOUR_TOKEN"

# Accept invitation
curl -X POST http://localhost:8000/api/user/invitations/1/accept \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### Rate Limiting

Project API keys can have rate limits:

```json
{
  "name": "Limited Access Key",
  "rate_limit_per_hour": 50
}
```

When limit is reached, requests return:
```json
{
  "detail": "Invalid API key, expired, or rate limited"
}
```

---

## Authentication

The API supports three authentication methods:

### 1. JWT Token (Bearer)

1. Login via `/api/auth/login`
2. Include token in requests: `Authorization: Bearer <token>`
3. Tokens expire after 24 hours

### 2. User API Key

1. Create key via `/api/user/api-keys`
2. Include key in requests: `X-API-Key: <key>`
3. Keys can have custom expiration
4. Full access to all user's projects

### 3. Project API Key

1. Create key via `/api/projects/{id}/api-keys`
2. Include key in requests: `X-Project-Key: <key>`
3. Use endpoints with `/key` suffix (e.g., `/query/key`)
4. Limited to specific project with fine-grained permissions

---

## Project Database Types

| Type | db_uri Format | Example |
|------|---------------|---------|
| SQLite | File path | `/path/to/database.db` |
| PostgreSQL | Connection string | `postgresql://user:pass@host:5432/dbname` |

---

## LLM Providers

Configure LLM provider per project:

### Ollama (Default)

```json
{
  "llm_provider": "ollama",
  "llm_model": "llama3.2:3b"
}
```

### Anthropic Claude

```json
{
  "llm_provider": "anthropic",
  "llm_model": "claude-sonnet-4-5-20250929",
  "llm_api_key": "sk-ant-..."
}
```

### OpenAI

```json
{
  "llm_provider": "openai",
  "llm_model": "gpt-4",
  "llm_api_key": "sk-..."
}
```

---

## Docker Deployment

### docker-compose.yml

```yaml
version: '3.8'

services:
  qasql-api:
    build: .
    container_name: qasql-api
    ports:
      - "8000:8000"
    volumes:
      - ./data:/data
      - ./qasql_api_output:/app/qasql_api_output
    environment:
      - QASQL_SECRET_KEY=your-secret-key-here
      - QASQL_LLM_PROVIDER=ollama
      - QASQL_OLLAMA_URL=http://ollama:11434
    depends_on:
      - ollama
    restart: unless-stopped

  ollama:
    image: ollama/ollama
    container_name: qasql-ollama
    ports:
      - "11434:11434"
    volumes:
      - ollama_data:/root/.ollama
    restart: unless-stopped

volumes:
  ollama_data:
```

### Build and Run

```bash
docker-compose up -d

# Pull Ollama model
docker exec qasql-ollama ollama pull llama3.2:3b
```

---

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `QASQL_SECRET_KEY` | JWT signing secret | Random generated |
| `QASQL_LLM_PROVIDER` | Default LLM provider | `ollama` |
| `QASQL_LLM_MODEL` | Default LLM model | `llama3.2:3b` |
| `QASQL_OLLAMA_URL` | Ollama server URL | `http://localhost:11434` |
| `ANTHROPIC_API_KEY` | Anthropic API key | - |
| `OPENAI_API_KEY` | OpenAI API key | - |

---

## Server Options

```
python app.py --help

Options:
  --host            Host to bind (default: 127.0.0.1)
  --port, -p        Port to bind (default: 8000)
  --platform-db     Platform database path (default: ./qasql_platform.db)
  --provider        Default LLM provider: ollama, anthropic, openai
  --model, -m       Default LLM model
  --ollama-url      Ollama server URL
  --reload          Enable auto-reload (development)
```

---

## Python Client Example

```python
import requests

API_URL = "http://localhost:8000"

# Register
requests.post(f"{API_URL}/api/auth/register", json={
    "email": "user@example.com",
    "username": "myuser",
    "password": "secret123"
})

# Login
response = requests.post(f"{API_URL}/api/auth/login", data={
    "username": "myuser",
    "password": "secret123"
})
token = response.json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}

# Create project
response = requests.post(f"{API_URL}/api/projects", headers=headers, json={
    "name": "My Database",
    "db_type": "sqlite",
    "db_uri": "/path/to/db.sqlite"
})
project_id = response.json()["id"]

# Setup project
requests.post(f"{API_URL}/api/projects/{project_id}/setup", headers=headers)

# Generate SQL
response = requests.post(
    f"{API_URL}/api/projects/{project_id}/query",
    headers=headers,
    json={"question": "How many users signed up last month?"}
)
print(response.json()["sql"])
```

---

## Security Notes

- Use `QASQL_SECRET_KEY` environment variable in production
- Default binding is `127.0.0.1` (localhost only)
- Use HTTPS via reverse proxy (nginx, traefik) in production
- API keys should be stored securely by clients
- Database URIs may contain credentials - handle with care

---

## License

MIT License
