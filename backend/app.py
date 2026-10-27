import os
import io
import re
import faiss
import numpy as np

from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pypdf import PdfReader
from sentence_transformers import SentenceTransformer
from mistralai.client import Mistral
from storage import init_db, get_connection, DATA_DIR


# --------------------------------------------------
# 1. Configuration
# --------------------------------------------------

load_dotenv()

api_key = os.getenv("MISTRAL_API_KEY")

if not api_key:
    raise ValueError("MISTRAL_API_KEY missing in .env")

client = Mistral(api_key=api_key)

MODEL = "ministral-8b-2512"
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

SIMILARITY_THRESHOLD = 0.25
MAX_CHUNKS_PER_SOURCE = 8
MAX_CONTEXT_CHUNKS = 24
MAX_FILE_SIZE = 10 * 1024 * 1024


SYSTEM_PROMPT = """
You are RANGER, a reliable documentation assistant.

EVIDENCE RULES:

1. Answer document-specific questions using the supplied excerpts.
2. Never invent company-specific facts, API endpoints, HTTP methods,
   parameters, status codes, or policies.
3. Clearly distinguish supported facts from unknown information.
4. When asked about endpoints or URLs, list only examples actually
   supported by the supplied excerpts.
5. Examples do not establish a complete endpoint count unless the
   documentation explicitly establishes a complete inventory.
6. If the excerpts partially answer a question, explain what is known
   and what cannot be determined.
7. Never invent page numbers, quotations, or citations.
8. Cite source filenames when supported by the excerpts.
9. Do not claim to have searched the entire document when only excerpts
   were retrieved.
10. If the excerpts do not provide enough evidence, say so.
11. Never provide general-knowledge solutions, implementation steps,
    tools, code, or recommendations that are not supported by the
    supplied documentation excerpts.

12. If the question asks how to perform something that the excerpts
    do not explain, state what the excerpts cover and clearly say
    that the requested implementation details are not documented.
    Do not add an unsupported "Next Steps" section.
RESPONSE STYLE:
- Answer directly and clearly.
- Use headings and bullet points when helpful.
- Do not use outside knowledge to fill gaps in document-specific answers.
"""


# --------------------------------------------------
# 2. FastAPI application
# --------------------------------------------------

app = FastAPI(title="RANGER - AI Documentation Assistant")

UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------
# 3. Load embedding model
# --------------------------------------------------

print("Loading embedding model...")

embedding_model = SentenceTransformer(EMBEDDING_MODEL)

print("Embedding model loaded successfully!")


# --------------------------------------------------
# 4. In-memory storage
# --------------------------------------------------

chunks = []
sources = set()
faiss_index = None


# --------------------------------------------------
# 5. Request and response models
# --------------------------------------------------

class ChatRequest(BaseModel):
    question: str


class ChatResponse(BaseModel):
    answer: str
    sources: list[str] = Field(default_factory=list)


# --------------------------------------------------
# 6. Split document text into chunks
# --------------------------------------------------

def split_text(text, chunk_size=180, overlap=40):
    words = text.split()
    result = []

    step = chunk_size - overlap

    for start in range(0, len(words), step):
        chunk_text = " ".join(words[start:start + chunk_size])

        if chunk_text:
            result.append(chunk_text)

    return result


# --------------------------------------------------
# 7. Extract text from files
# --------------------------------------------------

def extract_text(filename, content):
    extension = Path(filename).suffix.lower()

    if extension == ".pdf":
        reader = PdfReader(io.BytesIO(content))

        return "\n".join(
            page.extract_text() or ""
            for page in reader.pages
        )

    if extension in {".txt", ".md"}:
        return content.decode(
            "utf-8-sig",
            errors="replace",
        )

    raise HTTPException(
        status_code=400,
        detail="Unsupported file type. Upload PDF, TXT, or MD files.",
    )


# --------------------------------------------------
# 8. Rebuild FAISS index
# --------------------------------------------------

def rebuild_faiss_index():
    global faiss_index

    if not chunks:
        faiss_index = None
        print("No document chunks available for indexing.")
        return

    print("Generating embeddings for document chunks...")

    texts = [item["text"] for item in chunks]

    embeddings = embedding_model.encode(
        texts,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )

    embeddings = np.asarray(
        embeddings,
        dtype=np.float32,
    )

    # Normalized vectors + inner product = cosine similarity.
    faiss_index = faiss.IndexFlatIP(
        embeddings.shape[1]
    )

    faiss_index.add(embeddings)

    print("FAISS index built successfully!")
    print("Indexed chunks:", faiss_index.ntotal)


# --------------------------------------------------
# 9. Load persisted documents on startup
# --------------------------------------------------

@app.on_event("startup")
def startup_event():
    global chunks, sources

    init_db()

    with get_connection() as connection:
        rows = connection.execute("""
            SELECT d.filename, c.text
            FROM documents d
            JOIN document_chunks c
                ON d.id = c.document_id
            ORDER BY d.id, c.chunk_index
        """).fetchall()

    chunks = [
        {
            "source": row["filename"],
            "text": row["text"],
        }
        for row in rows
    ]

    sources = {
        item["source"]
        for item in chunks
    }

    rebuild_faiss_index()

    print(f"SQLite loaded: {len(sources)} documents")
    print(f"FAISS indexed: {len(chunks)} chunks")


# --------------------------------------------------
# 10. Home endpoint
# --------------------------------------------------

@app.get("/")
def home():
    return {
        "message": "RANGER AI Documentation Assistant is running!",
        "retrieval": "FAISS vector search",
        "indexed_chunks": (
            faiss_index.ntotal
            if faiss_index is not None
            else 0
        ),
    }


# --------------------------------------------------
# 11. Upload document
# --------------------------------------------------

@app.post("/upload")
async def upload_document(file: UploadFile = File(...)):
    global chunks, sources

    filename = Path(file.filename or "document").name
    content = await file.read()

    if not content:
        raise HTTPException(
            status_code=400,
            detail="The file is empty.",
        )

    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail="Maximum file size is 10 MB.",
        )

    text = extract_text(filename, content)

    if not text.strip():
        raise HTTPException(
            status_code=400,
            detail="No readable text found in this file.",
        )

    new_chunks = split_text(text)

    if not new_chunks:
        raise HTTPException(
            status_code=400,
            detail="No text chunks could be created.",
        )

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    file_path = UPLOAD_DIR / filename

    try:
        file_path.write_bytes(content)

        with get_connection() as connection:
            # Replace an existing file with the same filename.
            connection.execute(
                "DELETE FROM documents WHERE filename = ?",
                (filename,),
            )

            cursor = connection.execute(
                """
                INSERT INTO documents (filename, stored_path)
                VALUES (?, ?)
                """,
                (filename, str(file_path)),
            )

            document_id = cursor.lastrowid

            connection.executemany(
                """
                INSERT INTO document_chunks
                    (document_id, chunk_index, text)
                VALUES (?, ?, ?)
                """,
                [
                    (document_id, index, chunk_text)
                    for index, chunk_text in enumerate(new_chunks)
                ],
            )

        with get_connection() as connection:
            rows = connection.execute("""
                SELECT d.filename, c.text
                FROM documents d
                JOIN document_chunks c
                    ON d.id = c.document_id
                ORDER BY d.id, c.chunk_index
            """).fetchall()

        chunks = [
            {
                "source": row["filename"],
                "text": row["text"],
            }
            for row in rows
        ]

        sources = {
            item["source"]
            for item in chunks
        }

        rebuild_faiss_index()

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save or index document: {error}",
        )

    return {
        "message": "Document saved and indexed successfully!",
        "filename": filename,
        "chunks_added": len(new_chunks),
        "total_chunks": len(chunks),
        "documents": sorted(sources),
        "indexed_chunks": (
            faiss_index.ntotal
            if faiss_index is not None
            else 0
        ),
    }


# --------------------------------------------------
# 12. List uploaded documents
# --------------------------------------------------

@app.get("/documents")
def list_documents():
    return {
        "documents": sorted(sources),
        "total_chunks": len(chunks),
        "indexed_chunks": (
            faiss_index.ntotal
            if faiss_index is not None
            else 0
        ),
    }


# --------------------------------------------------
# 13. Delete document
# --------------------------------------------------

@app.delete("/documents/{filename}")
def delete_document(filename: str):
    global chunks, sources

    with get_connection() as connection:
        document = connection.execute(
            """
            SELECT id, stored_path
            FROM documents
            WHERE filename = ?
            """,
            (filename,),
        ).fetchone()

        if document is None:
            raise HTTPException(
                status_code=404,
                detail="Document not found.",
            )

        stored_path = Path(document["stored_path"])

        connection.execute(
            "DELETE FROM documents WHERE id = ?",
            (document["id"],),
        )

    try:
        if stored_path.exists():
            stored_path.unlink()
    except OSError as error:
        print(f"Warning: Could not remove uploaded file: {error}")

    with get_connection() as connection:
        rows = connection.execute("""
            SELECT d.filename, c.text
            FROM documents d
            JOIN document_chunks c
                ON d.id = c.document_id
            ORDER BY d.id, c.chunk_index
        """).fetchall()

    chunks = [
        {
            "source": row["filename"],
            "text": row["text"],
        }
        for row in rows
    ]

    sources = {
        item["source"]
        for item in chunks
    }

    rebuild_faiss_index()

    return {
        "message": "Document deleted successfully.",
        "deleted_document": filename,
        "remaining_documents": sorted(sources),
        "total_chunks": len(chunks),
        "indexed_chunks": (
            faiss_index.ntotal
            if faiss_index is not None
            else 0
        ),
    }


# --------------------------------------------------
# 14. Handle greetings
# --------------------------------------------------

def is_greeting(question):
    normalized = re.sub(
        r"[!.?,]+$",
        "",
        question.strip().lower(),
    )

    greetings = {
        "hi",
        "hello",
        "hey",
        "hai",
        "good morning",
        "good afternoon",
        "good evening",
    }

    return normalized in greetings


# --------------------------------------------------
# 15. Retrieve relevant document chunks
# --------------------------------------------------

def retrieve_chunks(question):
    if (
        not chunks
        or faiss_index is None
        or faiss_index.ntotal == 0
    ):
        return []

    question_embedding = embedding_model.encode(
        [question],
        convert_to_numpy=True,
        normalize_embeddings=True,
    )

    question_embedding = np.asarray(
        question_embedding,
        dtype=np.float32,
    )

    # Search all indexed chunks.
    scores, indices = faiss_index.search(
        question_embedding,
        faiss_index.ntotal,
    )

    best_by_source = {}

    for score, idx in zip(scores[0], indices[0]):
        if idx < 0:
            continue

        score = float(score)
        item = chunks[int(idx)]
        source = item["source"]

        print(
            f"SCORE: {score:.4f} | SOURCE: {source}"
        )

        if score < SIMILARITY_THRESHOLD:
            continue

        best_by_source.setdefault(
            source,
            [],
        ).append((score, item))

    # Keep the best chunks per document.
    ranked = []

    for source, matches in best_by_source.items():
        matches.sort(
            key=lambda pair: pair[0],
            reverse=True,
        )

        ranked.extend(
            matches[:MAX_CHUNKS_PER_SOURCE]
        )

    ranked.sort(
        key=lambda pair: pair[0],
        reverse=True,
    )

    result = [
        item
        for score, item in ranked[:MAX_CONTEXT_CHUNKS]
    ]

    # Supplement semantic search with exact technical keywords.
    technical_terms = re.findall(
        r"\b(?:endpoint|endpoints|url|urls|uri|uris|http|https|"
        r"get|post|put|patch|delete|service document|metadata|"
        r"entityset|entity set|api|apis|route|routes)\b",
        question.lower(),
    )

    if technical_terms:
        existing = {
            (item["source"], item["text"])
            for item in result
        }

        keyword_matches = []

        for item in chunks:
            text_lower = item["text"].lower()

            hits = sum(
                1
                for term in set(technical_terms)
                if term in text_lower
            )

            if hits:
                keyword_matches.append((hits, item))

        keyword_matches.sort(
            key=lambda pair: pair[0],
            reverse=True,
        )

        for hits, item in keyword_matches:
            key = (item["source"], item["text"])

            if key not in existing:
                result.append(item)
                existing.add(key)

            if len(result) >= MAX_CONTEXT_CHUNKS:
                break

    result = result[:MAX_CONTEXT_CHUNKS]

    print("RETRIEVED CHUNKS:", len(result))
    print(
        "RETRIEVED SOURCES:",
        sorted({
            item["source"]
            for item in result
        }),
    )

    return result


# --------------------------------------------------
# 16. Chat endpoint
# --------------------------------------------------

@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    question = request.question.strip()

    if not question:
        raise HTTPException(
            status_code=400,
            detail="Question cannot be empty.",
        )

    # Handle greetings before document search.
    if is_greeting(question):
        return {
            "answer": (
                "Hey! 👋 I'm RANGER. Ask me anything "
                "about your uploaded documentation."
            ),
            "sources": [],
        }

    print("\nQUESTION:", question)
    print("UPLOADED SOURCES:", sorted(sources))

    relevant = retrieve_chunks(question)

    if relevant:
        context = "\n\n".join(
            f"Source: {item['source']}\n"
            f"Excerpt: {item['text']}"
            for item in relevant
        )

        user_prompt = f"""
Answer the user's question using the documentation excerpts below.

Rules:
1. Use the excerpts as evidence for document-specific claims.
2. For endpoint, URL, route, or HTTP-method questions, list only examples
   that actually appear in the excerpts.
3. Do not assume examples form a complete inventory unless the excerpts
   explicitly establish that they are complete.
4. Give an exact endpoint count only when the excerpts support a complete
   count. Otherwise explain that the total cannot be determined.
5. If the excerpts partially answer the question, give supported details
   and identify what remains unknown.
6. Cite source filenames next to relevant details.
7. Do not use general industry knowledge to fill gaps.

DOCUMENTATION EXCERPTS:
{context}

QUESTION:
{question}
"""
    else:
        user_prompt = f"""
No sufficiently relevant documentation excerpts were retrieved.

Question:
{question}

Explain briefly that the uploaded documentation excerpts do not provide
enough evidence to answer this document-specific question. Do not invent
facts, endpoints, or company policies.
"""

    try:
        response = client.chat.complete(
            model=MODEL,
            messages=[
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": user_prompt,
                },
            ],
        )

        answer = response.choices[0].message.content

        source_list = list(dict.fromkeys(
            item["source"]
            for item in relevant
        ))

        return {
            "answer": str(answer),
            "sources": source_list,
        }

    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=f"AI provider request failed: {error}",
        )