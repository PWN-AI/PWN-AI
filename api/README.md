# PWN-AI API

Express.js TypeScript backend for the PWN-AI pentesting framework. Provides REST API endpoints that communicate with the Python AI framework.

## Architecture

```
User Request → Express.js API → Python AI (main.py) → Ollama → AI Model
```

The backend does NOT contact Ollama directly. Instead, it spawns the Python AI code which handles all LLM communication using the same configuration as `main.py`.

## Features

- 🔐 JWT-based authentication
- 💬 Chat completions via Python AI
- 🎯 Full pentest session execution
- 📋 Model listing from AI config
- 🛡️ Security middleware (CORS, Helmet)
- 📝 TypeScript for type safety

## Prerequisites

- Node.js 18+
- Python 3.x with the AI dependencies installed (see `ai/pyproject.toml`)
- Ollama running locally (`ollama serve`)
- At least one Ollama model pulled (e.g., `ollama pull qwen2.5-coder:7b`)

## Installation

```bash
cd api
npm install
```

## Configuration

Copy `.env.example` to `.env` and configure:

```bash
cp .env.example .env
```

Environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | 3000 | Server port |
| `NODE_ENV` | development | Environment mode |
| `JWT_SECRET` | (required) | Secret key for JWT signing |
| `JWT_EXPIRES_IN` | 24h | Token expiration time |
| `OLLAMA_BASE_URL` | http://localhost:11434 | Ollama API URL |
| `OLLAMA_MODEL` | qwen2.5-coder:7b | Default model for chat |

## Running

### Development

```bash
npm run dev
```

### Production

```bash
npm run build
npm start
```

## API Endpoints

### Health Check (No Auth)

```
GET /api/health
```

Response:
```json
{
  "status": "ok",
  "timestamp": "2024-01-01T00:00:00.000Z"
}
```

### Authentication

#### Register

```
POST /api/auth/register
Content-Type: application/json

{
  "email": "user@example.com",
  "password": "password123"
}
```

Response:
```json
{
  "message": "User registered successfully",
  "user": {
    "id": "uuid",
    "email": "user@example.com",
    "createdAt": "2024-01-01T00:00:00.000Z"
  },
  "token": "jwt-token"
}
```

#### Login

```
POST /api/auth/login
Content-Type: application/json

{
  "email": "user@example.com",
  "password": "password123"
}
```

Response:
```json
{
  "message": "Login successful",
  "user": { ... },
  "token": "jwt-token"
}
```

#### Get Current User

```
GET /api/auth/me
Authorization: Bearer <token>
```

### Chat (Requires Auth)

#### Send Message

Sends messages to the Python AI which uses the LLM configured in `ai/config/models.local.yaml`.

```
POST /api/chat/completions
Authorization: Bearer <token>
Content-Type: application/json

{
  "messages": [
    { "role": "system", "content": "You are a helpful assistant." },
    { "role": "user", "content": "Hello!" }
  ]
}
```

Response:
```json
{
  "id": "uuid",
  "model": "kimi-k2.5",
  "message": {
    "role": "assistant",
    "content": "Hello! How can I help you today?"
  },
  "created_at": 1704067200000
}
```

#### Run Pentest Session

Executes a full pentest session using `main.py`.

```
POST /api/chat/session
Authorization: Bearer <token>
Content-Type: application/json

{
  "goal": "Find SQL injection vulnerabilities",
  "targetUrl": "http://example.com/app",
  "username": "admin",
  "password": "password123",
  "autoApprove": true
}
```

Response:
```json
{
  "sessionId": "uuid",
  "output": "... session output ...",
  "status": "completed"
}
```

#### List Models

```
GET /api/chat/models
Authorization: Bearer <token>
```

Response (reads from `ai/config/models.local.yaml`):
```json
{
  "models": [
    {
      "name": "kimi-k2.5",
      "role": "head",
      "provider": "cloud"
    },
    {
      "name": "glm-5:cloud",
      "role": "implementor",
      "provider": "cloud"
    }
  ]
}
```

#### Check AI Connection

```
GET /api/chat/health
Authorization: Bearer <token>
```

Response:
```json
{
  "status": "ok",
  "ai": "connected"
}
```

## Project Structure

```
api/
├── src/
│   ├── index.ts              # Entry point
│   ├── app.ts                # Express app setup
│   ├── config/
│   │   └── index.ts          # Environment config
│   ├── middleware/
│   │   ├── auth.ts           # JWT auth middleware
│   │   └── errorHandler.ts   # Global error handler
│   ├── routes/
│   │   ├── index.ts          # Route aggregator
│   │   ├── auth.routes.ts    # Auth endpoints
│   │   └── chat.routes.ts    # Chat endpoints
│   ├── controllers/
│   │   ├── auth.controller.ts
│   │   └── chat.controller.ts
│   ├── services/
│   │   ├── auth.service.ts   # Auth logic
│   │   └── ollama.service.ts # Ollama API client
│   ├── models/
│   │   └── user.model.ts     # In-memory user store
│   └── types/
│       └── index.ts          # TypeScript interfaces
├── package.json
├── tsconfig.json
├── .env.example
└── README.md
```

## Error Responses

All errors follow this format:

```json
{
  "error": "Error message"
}
```

Common status codes:
- `400` - Bad request (validation errors)
- `401` - Unauthorized (missing/invalid token)
- `409` - Conflict (e.g., user already exists)
- `503` - Service unavailable (Python AI not available)

## How It Works

1. **Chat Completions**: The backend spawns a Python process that imports `ai.llm.ollama_client`, builds the LLM clients using `ai/config/models.local.yaml`, and invokes the model.

2. **Pentest Sessions**: The backend runs `python main.py --goal "..." --target-url "..."` which executes the full AI pentesting workflow.

3. **No Direct Ollama Contact**: All LLM communication goes through the Python AI code, ensuring consistency with how you run `python main.py`.

## Notes

- User data is stored in-memory and will be lost on restart
- For production, replace in-memory storage with a database (MongoDB, PostgreSQL)
- Change `JWT_SECRET` to a strong random value in production
- The AI models are configured in `ai/config/models.local.yaml`, not in the Express backend
