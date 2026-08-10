# Ocean-Intelligence-

## Supabase SQL Chat

A full-stack chatbot that uses AI to classify questions and intelligently routes them—data questions are converted to SELECT SQL and executed on Supabase, while general questions get direct LLM answers. Ambiguous questions now trigger a clarifying follow-up, and SQL generation retries on parse/validation failure.

### Architecture

- **`backend/`**: FastAPI app with query classifier and SQL generator using Groq API
  - `app/main.py`: Routes requests through classifier
  - `app/sql_chat.py`: Query classifier, SQL generator, SQL validator, retry logic, and executor
  - `app/openai_client.py`: LLM client abstraction (Groq or OpenAI)
  - `requirements.txt`: Backend dependencies (FastAPI, Groq SDK, psycopg, sqlglot, etc.)
- **`frontend/`**: Vite + React chat UI (TypeScript)
  - Simplified chat interface for asking questions
  - Displays SQL queries and results when applicable
  - `package.json`: Frontend dependencies (React, TypeScript, Vite)
- **`scripts/`**: AWS Glue ETL job (`glue_transform.py`)
  - Existing data pipeline for daily processing
- **`infra/`**: CloudFormation template for AWS pipeline

### Prerequisites

- Python 3.12+
- Node.js 18+
- **API Key**: `GROQ_API_KEY` in your `.env` (for Groq LLM) or `OPENAI_API_KEY` (fallback)
- **Database** (optional for testing): `SUPABASE_DB_URL` in your `.env`
- **Schema source**: `Data_glossary.md` in the repo root is used to build the schema summary for SQL generation

### Local Setup

#### 1. Install Python dependencies

There are two `requirements.txt` files:

- **`/requirements.txt`** (root): Full stack dependencies including PySpark and AWS tools for the Glue pipeline
- **`/backend/requirements.txt`** (backend-only): Minimal backend-only dependencies for the chat API

Choose based on your needs:

```bash
# For full stack (Glue pipeline + backend + frontend)
pip install -r requirements.txt

# OR for backend + frontend only
pip install -r backend/requirements.txt
```

#### 2. Install frontend dependencies

```bash
cd frontend
npm install
```

#### 3. Set up environment

Copy `.env.example` to `.env` and configure:

```bash
# Required
GROQ_API_KEY=your_groq_api_key

# Optional (for database features)
SUPABASE_DB_URL=postgresql://user:password@host:port/db
```

### Start the Application

**Terminal 1: Backend**

```bash
uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
```

**Terminal 2: Frontend**

```bash
cd frontend
npm run dev
```

Then open `http://localhost:5173` in your browser.

### How It Works

1. **Query Classification**: User sends a question
   - Groq LLM determines if query needs database SQL or direct answer
   - Examples:
     - ✅ SQL needed: "How many users in the database?"
     - ❌ SQL NOT needed: "What is the capital of France?"

2. **Direct LLM Answer** (if no SQL needed)
   - Returns conversational response from Groq
   - No database query executed

If the classifier confidence is too low or the question is underspecified, the API returns a clarifying question instead of guessing.

3. **SQL Execution** (if SQL needed)

- Schema context is loaded from `Data_glossary.md`
- Groq generates PostgreSQL SELECT query
- Query is validated for safety (no writes, DDL, command execution)
- The backend retries SQL generation if the first attempt fails validation or execution
- Query is executed on Supabase
- Results returned with SQL and row data

### What You Can Do

- Ask questions in plain English
- Get classified responses (direct answer or database query)
- View the SQL query generated for data questions
- See result rows from Supabase

### API Endpoints

- `GET /health` — Health check
- `POST /api/chat` — Classify query, optionally execute SQL, return answer + results
- Response may also include `needs_clarification` and `clarifying_question` when the question is ambiguous

Request:

```json
{ "question": "How many orders were placed this month?" }
```

Response (with SQL):

```json
{
  "answer": "25 orders were placed this month.",
  "sql": "SELECT COUNT(*) FROM orders WHERE DATE_PART('month', created_at) = DATE_PART('month', NOW())",
  "columns": ["count"],
  "rows": [{ "count": 25 }],
  "row_count": 1,
  "requires_sql": true
}
```

Response (direct LLM answer):

```json
{
  "answer": "Paris is the capital of France.",
  "sql": null,
  "columns": null,
  "rows": null,
  "row_count": null,
  "requires_sql": false
}
```

Response (clarification needed):

```json
{
  "answer": "Do you want vessel data or order data?",
  "sql": null,
  "columns": null,
  "rows": null,
  "row_count": null,
  "requires_sql": false,
  "needs_clarification": true,
  "clarifying_question": "Do you want vessel data or order data?",
  "confidence": 0.42
}
```

### SQL Safety

Deterministic SQL validation ensures only SELECT queries are allowed:

- ✅ Allowed: `SELECT`, `UNION`, `INTERSECT`, `EXCEPT` (read-only)
- ❌ Blocked by pattern matching:
  - Write operations: `INSERT`, `UPDATE`, `DELETE`
  - DDL: `CREATE`, `DROP`, `ALTER`, `TRUNCATE`
  - Command execution: `EXEC`, `EXECUTE`, stored procedures
  - Multi-statement: `; INSERT` or `-- DROP` comments
- A `LIMIT` is automatically added when needed

### Why Two `requirements.txt` Files?

1. **`/requirements.txt`** (root directory)
   - Contains dependencies for the **complete stack** including the AWS Glue pipeline
   - Includes: PySpark, boto3, pandas, openpyxl (for ETL processing)
   - Use this if you're running the full system with Glue jobs

2. **`/backend/requirements.txt`** (backend directory)
   - Contains only **backend-specific dependencies** for the chat API
   - Smaller, focused set: FastAPI, uvicorn, Groq SDK, psycopg, sqlglot
   - Use this if you only need the chat backend (lighter install)

## Daily AWS pipeline

The ETL logic lives in `scripts/glue_transform.py`. A CloudFormation template is available at `infra/smu_daily_pipeline.yaml` to wire up:

- EventBridge daily schedule
- Lambda trigger
- Glue job execution
- DynamoDB checkpoint table for processed XLSX files

### How it works

1. EventBridge triggers Lambda on a daily cron.
2. Lambda lists `.xlsx` files in the bronze bucket and skips keys already recorded in DynamoDB.
3. Lambda starts the Glue job only when there are new files.
4. The Glue job processes the input files, writes JSON to the silver bucket, and records processed keys.
5. `orders.json` uses a deterministic Spark-generated `order_id` based on order details, so reruns can update in place.

### Deploy inputs

The CloudFormation stack needs:

- `BronzeBucketName`
- `SilverBucketName`
- `GlueScriptS3Uri` pointing to `scripts/glue_transform.py` uploaded in S3
- Optional bronze/silver prefixes if your files are not at the bucket root
