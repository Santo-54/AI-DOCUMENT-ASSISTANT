import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import "./App.css";

const API = "http://127.0.0.1:8000";

function App() {
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [documents, setDocuments] = useState([]);
  const [notice, setNotice] = useState("");
  const fileInput = useRef(null);
  const chatEnd = useRef(null);

  async function loadDocuments() {
    try {
      const response = await fetch(`${API}/documents`);
      if (!response.ok) return;

      const data = await response.json();
      setDocuments(data.documents || []);
    } catch {
      setNotice("Cannot connect to the backend. Make sure FastAPI is running.");
    }
  }

  useEffect(() => {
    loadDocuments();
  }, []);

  useEffect(() => {
    chatEnd.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  async function uploadDocument(event) {
    const file = event.target.files?.[0];
    event.target.value = "";

    if (!file) return;

    const formData = new FormData();
    formData.append("file", file);
    setUploading(true);
    setNotice("");

    try {
      const response = await fetch(`${API}/upload`, {
        method: "POST",
        body: formData,
      });

      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || "Upload failed");
      }

      await loadDocuments();
      setNotice(`✓ ${data.filename} uploaded successfully`);
    } catch (error) {
      setNotice(`Upload failed: ${error.message}`);
    } finally {
      setUploading(false);
    }
  }

  async function deleteDocument(filename) {
    const confirmed = window.confirm(
      `Delete "${filename}" and its indexed content?`
    );

    if (!confirmed) return;

    setNotice("");

    try {
      const response = await fetch(
        `${API}/documents/${encodeURIComponent(filename)}`,
        { method: "DELETE" }
      );

      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || "Delete failed");
      }

      await loadDocuments();
      setNotice(`✓ ${filename} deleted successfully`);
    } catch (error) {
      setNotice(`Delete failed: ${error.message}`);
    }
  }

  async function sendMessage(event) {
    event.preventDefault();
    const text = question.trim();

    if (!text || loading) return;

    setMessages((previous) => [
      ...previous,
      { role: "user", content: text },
    ]);
    setQuestion("");
    setLoading(true);

    try {
      const response = await fetch(`${API}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: text }),
      });

      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || "Request failed");
      }

      setMessages((previous) => [
        ...previous,
        {
          role: "assistant",
          content: data.answer,
          sources: data.sources || [],
        },
      ]);
    } catch (error) {
      setMessages((previous) => [
        ...previous,
        { role: "assistant", content: `**Error:** ${error.message}` },
      ]);
    } finally {
      setLoading(false);
    }
  }

  function askSuggestion(text) {
    setQuestion(text);
  }

  return (
    <main className="ranger-app">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">R</div>
          <div>
            <h1>RANGER</h1>
            <span>AI DOCUMENTATION ASSISTANT</span>
          </div>
        </div>

        <button
          className="new-chat-button"
          onClick={() => {
            setMessages([]);
            setQuestion("");
            setNotice("");
          }}
        >
          <span>＋</span> New conversation
        </button>

        <div className="sidebar-heading">
          <span>DOCUMENT LIBRARY</span>
          <span className="document-count">{documents.length}</span>
        </div>

        <button
          className="upload-zone"
          onClick={() => fileInput.current?.click()}
          disabled={uploading}
        >
          <span className="upload-icon">{uploading ? "↻" : "↑"}</span>
          <strong>{uploading ? "Uploading document..." : "Upload documents"}</strong>
          <small>PDF, TXT or Markdown</small>
        </button>

        <input
          ref={fileInput}
          type="file"
          accept=".pdf,.txt,.md"
          onChange={uploadDocument}
          hidden
        />

        <div className="document-list">
          {documents.length === 0 ? (
            <p className="empty-documents">
              No documents yet. Upload a file to get started.
            </p>
          ) : (
            documents.map((document, index) => {
              const filename =
                typeof document === "string"
                  ? document
                  : document.filename;

              return (
                <div className="document-item" key={filename}>
                  <span className="file-icon">▤</span>
                  <span className="document-name" title={filename}>
                    {filename}
                  </span>
                  <button
                    className="delete-button"
                    title={`Delete ${filename}`}
                    aria-label={`Delete ${filename}`}
                    onClick={() => deleteDocument(filename)}
                  >
                    ×
                  </button>
                </div>
              );
            })
          )}
        </div>

        <div className="sidebar-bottom">
          <div className="status-dot" />
          <span>Documentation workspace</span>
        </div>
      </aside>

      <section className="main-panel">
        <header className="topbar">
          <div>
            <span className="breadcrumb">WORKSPACE / </span>
            <span className="breadcrumb-current">AI ASSISTANT</span>
          </div>
          <div className="system-status">
            <span className="status-dot" />
            Local API
          </div>
        </header>

        <section className="chat-area">
          {messages.length === 0 ? (
            <div className="welcome-screen">
              <div className="welcome-symbol">
                <div className="welcome-symbol-inner">R</div>
              </div>

              <div className="eyebrow">YOUR DOCUMENTATION, UNDERSTOOD</div>
              <h2>
                Explore your docs.
                <br />
                <span>Find your answers.</span>
              </h2>
              <p className="welcome-description">
                Ask technical questions, understand complex concepts, and
                compare information across your uploaded documents.
              </p>

              <div className="suggestion-grid">
                <button
                  className="suggestion-card"
                  onClick={() =>
                    askSuggestion("Summarize the key points in my documents.")
                  }
                >
                  <span>⌕</span>
                  <strong>Summarize documents</strong>
                  <small>Get the important points quickly</small>
                  <span className="suggestion-arrow">↗</span>
                </button>

                <button
                  className="suggestion-card"
                  onClick={() =>
                    askSuggestion("Explain the main concepts in simple terms.")
                  }
                >
                  <span>◇</span>
                  <strong>Explain a concept</strong>
                  <small>Make technical topics easier to understand</small>
                  <span className="suggestion-arrow">↗</span>
                </button>

                <button
                  className="suggestion-card"
                  onClick={() =>
                    askSuggestion(
                      "Compare the purpose of the uploaded documents."
                    )
                  }
                >
                  <span>⇄</span>
                  <strong>Compare documents</strong>
                  <small>Discover similarities and differences</small>
                  <span className="suggestion-arrow">↗</span>
                </button>

                <button
                  className="suggestion-card"
                  onClick={() =>
                    askSuggestion("Show me important technical details and examples.")
                  }
                >
                  <span>⌘</span>
                  <strong>Find technical details</strong>
                  <small>Look for useful facts and examples</small>
                  <span className="suggestion-arrow">↗</span>
                </button>
              </div>

              <div className="disclaimer">
                <span>ⓘ</span>
                <p>
                  <strong>AI-generated answers may be inaccurate.</strong>{" "}
                  Verify important technical details against the original
                  documentation before using them in development or production.
                  RANGER does not replace official documentation or security reviews.
                </p>
              </div>
            </div>
          ) : (
            <div className="conversation">
              {messages.map((message, index) => (
                <article
                  className={`message-row ${message.role}`}
                  key={index}
                >
                  <div className={`avatar ${message.role}`}>
                    {message.role === "user" ? "Y" : "✦"}
                  </div>

                  <div className="message-body">
                    <div className="message-author">
                      {message.role === "user" ? "You" : "RANGER"}

                      {message.role === "assistant" && (
                        <span className="ai-label">AI RESPONSE</span>
                      )}
                    </div>

                    <div className="message-content">
                      <ReactMarkdown>{message.content}</ReactMarkdown>
                    </div>

                    {message.sources?.length > 0 && (
                      <div className="sources">
                        <span className="sources-heading">SOURCES</span>

                        {message.sources.map((source) => (
                          <span className="source-chip" key={source}>
                            ▤ {source}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                </article>
              ))}

              {loading && (
                <div className="message-row assistant">
                  <div className="avatar assistant">R</div>
                  <div className="message-body">
                    <div className="message-author">RANGER</div>
                    <div className="thinking-indicator">
                      <span />
                      <span />
                      <span />
                      <small>Searching your documentation...</small>
                    </div>
                  </div>
                </div>
              )}
              <div ref={chatEnd} />
            </div>
          )}
        </section>

        <footer className="composer-area">
          {notice && (
            <div className="notice" role="status">
              {notice}
              <button onClick={() => setNotice("")} aria-label="Dismiss notice">
                ×
              </button>
            </div>
          )}

          <form className="composer" onSubmit={sendMessage}>
            <textarea
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder="Ask RANGER anything about your documentation..."
              disabled={loading}
              rows={1}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  event.currentTarget.form.requestSubmit();
                }
              }}
            />
            <button
              className="send-button"
              type="submit"
              disabled={loading || !question.trim()}
              aria-label="Send message"
            >
              {loading ? "…" : "↑"}
            </button>
          </form>

          <div className="composer-hint">
            <span>↵ Enter to send</span>
            <span>⇧ Enter for a new line</span>
            <span>Powered by Mistral AI</span>
          </div>
        </footer>
      </section>
    </main>
  );
}

export default App;