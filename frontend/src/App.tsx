import { FormEvent, useState } from "react";

type ChatResponse = {
  answer: string;
  sql?: string;
  columns?: string[];
  rows?: Record<string, unknown>[];
  row_count?: number;
  needs_clarification?: boolean;
  clarifying_question?: string | null;
  confidence?: number | null;
};

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  sql?: string;
  rows?: Record<string, unknown>[];
  columns?: string[];
};

const welcomeMessage: ChatMessage = {
  id: "welcome",
  role: "assistant",
  content:
    "Ask me anything. I can answer questions about the database or general knowledge.",
};

function App() {
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([welcomeMessage]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const submitQuestion = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmedQuestion = question.trim();
    if (!trimmedQuestion || loading) {
      return;
    }

    const userMessage: ChatMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: trimmedQuestion,
    };

    setMessages((current) => [...current, userMessage]);
    setLoading(true);
    setError("");
    try {
      const response = await fetch("http://localhost:8000/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: trimmedQuestion,
        }),
      });

      if (!response.ok) {
        const errorBody = await response.json().catch(() => null);
        throw new Error(
          errorBody?.detail ?? `Query failed: ${response.status}`,
        );
      }

      const data = (await response.json()) as ChatResponse;
      const assistantMessage: ChatMessage = {
        id: crypto.randomUUID(),
        role: "assistant",
        content: data.answer,
        sql: data.sql,
        rows: data.rows,
        columns: data.columns,
      };
      setMessages((current) => [...current, assistantMessage]);
      setQuestion("");
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Unable to process your request.",
      );
      setMessages((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          content: "Sorry, I could not process your request.",
        },
      ]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="chat-shell">
      <section className="chat-layout">
        <div className="chat-panel">
          <div className="chat-header">
            <h1>Chat</h1>
            <p>
              Ask anything. I can query the database or answer general
              questions.
            </p>
          </div>

          <div className="chat-stream">
            {messages.map((message) => (
              <article
                key={message.id}
                className={`chat-message ${message.role}`}
              >
                <div className="chat-bubble">
                  <p>{message.content}</p>
                  {message.sql ? (
                    <details className="sql-details">
                      <summary>View SQL</summary>
                      <pre>{message.sql}</pre>
                    </details>
                  ) : null}
                  {message.rows && message.rows.length > 0 ? (
                    <div className="result-card">
                      <table>
                        <thead>
                          <tr>
                            {(
                              message.columns ??
                              Object.keys(message.rows[0] ?? {})
                            ).map((column) => (
                              <th key={column}>{column}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {message.rows.map((row, rowIndex) => (
                            <tr key={`${message.id}-${rowIndex}`}>
                              {(message.columns ?? Object.keys(row)).map(
                                (column) => (
                                  <td key={column}>
                                    {String(row[column] ?? "")}
                                  </td>
                                ),
                              )}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : null}
                </div>
              </article>
            ))}
          </div>

          <form className="composer" onSubmit={submitQuestion}>
            <textarea
              rows={3}
              placeholder="Ask me something..."
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
            />
            <button type="submit" disabled={loading || !question.trim()}>
              {loading ? "Thinking..." : "Send"}
            </button>
            {error ? <p className="error">{error}</p> : null}
          </form>
        </div>
      </section>
    </main>
  );
}

export default App;
