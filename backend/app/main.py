from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .schemas import ClassificationDebugRequest, ClassificationDebugResponse, SqlChatRequest, SqlChatResponse
from .sql_chat import build_answer, classify_query, run_sql_pipeline
from .openai_client import get_openai_client


CONFIDENCE_THRESHOLD = 0.65

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


@app.post("/api/debug/classification", response_model=ClassificationDebugResponse)
def debug_classification(payload: ClassificationDebugRequest) -> ClassificationDebugResponse:
    classification = classify_query(payload.question)
    confidence = float(classification.get("confidence", 0.0) or 0.0)
    return ClassificationDebugResponse(
        question=payload.question,
        needs_sql=bool(classification.get("needs_sql", False)),
        needs_clarification=bool(classification.get("needs_clarification", False)),
        clarifying_question=classification.get("clarification_question"),
        confidence=confidence,
        threshold=CONFIDENCE_THRESHOLD,
    )


@app.post("/api/chat", response_model=SqlChatResponse)
def chat(payload: SqlChatRequest) -> SqlChatResponse:
    try:
        classification = classify_query(payload.question)
        needs_sql = bool(classification.get("needs_sql", False))
        confidence = float(classification.get("confidence", 0.0) or 0.0)
        needs_clarification = bool(classification.get("needs_clarification", False))
        clarification_question = classification.get("clarification_question")

        if needs_clarification or confidence < CONFIDENCE_THRESHOLD:
            clarification_text = clarification_question or "Could you clarify what specific data you want me to look up?"
            return SqlChatResponse(
                answer=clarification_text,
                sql=None,
                columns=None,
                rows=None,
                row_count=None,
                requires_sql=False,
                needs_clarification=True,
                clarifying_question=clarification_text,
                confidence=confidence,
            )

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
                confidence=confidence,
            )

        sql, columns, rows = run_sql_pipeline(payload.question, 25)
        
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
        confidence=confidence,
    )
