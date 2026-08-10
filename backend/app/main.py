from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .schemas import SqlChatRequest, SqlChatResponse
from .sql_chat import build_answer, classify_query, execute_sql, generate_sql, get_schema_summary
from .openai_client import get_openai_client

app = FastAPI(title="Supabase SQL Chat API", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/chat", response_model=SqlChatResponse)
def chat(payload: SqlChatRequest) -> SqlChatResponse:
    try:
        # Classify if question needs SQL or just LLM answer
        needs_sql = classify_query(payload.question)
        
        if not needs_sql:
            # Get direct LLM answer without SQL execution
            client = get_openai_client()
            response = client.chat.completions.create(
                model="openai/gpt-oss-120b",
                temperature=0.7,
                messages=[
                    {
                        "role": "user",
                        "content": payload.question,
                    }
                ],
            )
            answer = response.choices[0].message.content or "I couldn't generate a response."
            return SqlChatResponse(
                answer=answer,
                sql=None,
                columns=None,
                rows=None,
                row_count=None,
                requires_sql=False,
            )
        
        # Generate and execute SQL
        schema_summary = get_schema_summary()
        sql = generate_sql(payload.question, schema_summary)
        columns, rows = execute_sql(sql, 25)
        
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - runtime/network dependent
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    row_count = len(rows)
    return SqlChatResponse(
        answer=build_answer(payload.question, row_count),
        sql=sql,
        columns=columns,
        rows=rows,
        row_count=row_count,
        requires_sql=True,
    )
