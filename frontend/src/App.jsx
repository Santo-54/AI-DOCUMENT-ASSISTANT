
import { useState } from "react";
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

      setDocuments(data.documents);
      setNotice(
        `Uploaded ${data.filename}: ${data.chunks_added} chunks added.`
      );
    } catch (error) {
      setNotice(`Upload error: ${error.message}`);
    } finally {
      setUploading(false);
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
        { role: "assistant", content: data.answer },
      ]);
    } catch (error) {
      setMessages((previous) => [
        ...previous,
        { role: "assistant", content: `Error: ${error.message}` },
      ]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="app">
      <header>
        <h1>Developer Documentation AI Assistant</h1>
        <p>Upload documentation and ask questions about it.</p>
      </header>

      <section className="documents">
        <label className="upload-button">
          {uploading ? "Uploading..." : "Upload PDF / TXT / MD"}
          <input
            type="file"
            accept=".pdf,.txt,.md"
            onChange={uploadDocument}
            disabled={uploading}
            hidden
          />
        </label>

        {notice && <p className="notice">{notice}</p>}

        {documents.length > 0 && (
          <div>
            <strong>Uploaded documents</strong>
            <ul>
              {documents.map((name) => <li key={name}>{name}</li>)}
            </ul>
          </div>
        )}
      </section>

      <section className="chat">
        {messages.length === 0 && (
          <div className="welcome">
            <h2>How can I help?</h2>
            <p>Upload a document, then ask a question about its content.</p>
          </div>
        )}

        {messages.map((message, index) => (
          <article className={`message ${message.role}`} key={index}>
            <strong>
              {message.role === "user" ? "You" : "Assistant"}
            </strong>
            <div className="message-content">
              <ReactMarkdown>{message.content}</ReactMarkdown>
            </div>
          </article>
        ))}

        {loading && <p className="loading">Assistant is thinking...</p>}
      </section>

      <form className="composer" onSubmit={sendMessage}>
        <input
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="Ask about your documentation..."
          disabled={loading}
        />
        <button disabled={loading || !question.trim()}>
          {loading ? "Thinking..." : "Send"}
        </button>
      </form>
    </main>
  );
}

export default App;
