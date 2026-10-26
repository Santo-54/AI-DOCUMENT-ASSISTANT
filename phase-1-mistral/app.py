import os
import io
import faiss
import numpy as np
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pypdf import PdfReader
from sentence_transformers import SentenceTransformer
from mistralai.client import Mistral
from pathlib import Path
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

TOP_K = 5

# Initial threshold for normalized cosine similarity.
# Tune this using real questions and retrieval tests.
SIMILARITY_THRESHOLD = 0.30




SYSTEM_PROMPT = """
You are a reliable, evidence-grounded Documentation AI Assistant.

STRICT EVIDENCE RULES:

1. Answer document-specific questions using ONLY the
   supplied documentation excerpts.

2. Do not add facts from general knowledge, training data,
   assumptions, or other documents.

3. Distinguish carefully between:
   - Explicitly stated facts
   - Reasonable interpretations directly supported by excerpts
   - Information not present in the excerpts

4. Never list an API method, endpoint, parameter, status code,
   security requirement, or behavior unless the excerpts
   explicitly support that detail.

5. If the question asks for a list, include only items
   explicitly supported by the supplied excerpts.
   Do not complete a partial list using general knowledge.

6. Do not invent page numbers, section names, quotations,
   source filenames, or citations.

7. A source filename does not prove that a claim is supported.
   Each document-specific claim must be backed by excerpt text.

8. If the excerpts do not establish the answer, say:
   "The provided documentation excerpts do not specify this."

9. If only part of the question can be answered, explain
   what the excerpts establish and what they do not establish.

10. Do not claim that you searched the entire document when
    only selected excerpts were retrieved.

RESPONSE STYLE:
- Answer the question directly.
- Use clear headings or bullets when helpful.
- Cite source filenames only when supported by retrieved excerpts.
- Do not include unsupported supplementary explanations.
"""




# --------------------------------------------------
# 2. FastAPI application
# --------------------------------------------------

app = FastAPI(
    title="Developer Documentation AI Assistant"
)

UPLOAD_DIR = DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

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
        {"source": row["filename"], "text": row["text"]}
        for row in rows
    ]

    sources = {chunk["source"] for chunk in chunks}

    rebuild_faiss_index()

    print(f"SQLite loaded: {len(sources)} documents")
    print(f"FAISS indexed: {len(chunks)} chunks")

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
# 3. Load embedding model once
# --------------------------------------------------

print("Loading embedding model...")

embedding_model = SentenceTransformer(EMBEDDING_MODEL)

print("Embedding model loaded successfully!")


# --------------------------------------------------
# 4. In-memory document storage and FAISS index
# --------------------------------------------------

# Each item contains:
# {"source": filename, "text": chunk_text}

chunks = []
sources = set()

# FAISS index for cosine similarity search.
# None means no documents have been indexed yet.
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
# 6. Split text into overlapping chunks
# --------------------------------------------------

def split_text(text, chunk_size=180, overlap=40):
    """Split text into overlapping word-based chunks."""

    words = text.split()
    result = []

    step = chunk_size - overlap

    for start in range(0, len(words), step):
        chunk_text = " ".join(
            words[start:start + chunk_size]
        )

        if chunk_text:
            result.append(chunk_text)

    return result


# --------------------------------------------------
# 7. Extract text from supported files
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
# 8. Build or rebuild the FAISS index
# --------------------------------------------------

def rebuild_faiss_index():
    """
    Create embeddings for all stored chunks and rebuild
    the FAISS index.

    FAISS vector positions correspond to positions in chunks.
    """

    global faiss_index

    if not chunks:
        faiss_index = None
        print("No document chunks available for indexing.")
        return

    print("Generating embeddings for document chunks...")

    texts = [
        item["text"]
        for item in chunks
    ]

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
# 9. Home endpoint
# --------------------------------------------------

@app.get("/")
def home():
    return {
        "message": "Developer Documentation AI Assistant is running!",
        "retrieval": "FAISS vector search",
        "indexed_chunks": (
            faiss_index.ntotal
            if faiss_index is not None
            else 0
        ),
    }


# --------------------------------------------------
# 10. Upload document endpoint
# --------------------------------------------------

@app.post("/upload")
async def upload_document(file: UploadFile = File(...)):
    global chunks, sources, faiss_index

    filename = Path(file.filename or "document").name
    content = await file.read()

    if not content:
        raise HTTPException(status_code=400, detail="The file is empty.")

    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Maximum file size is 10 MB.")

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

    # Save the uploaded file to disk.
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    file_path = UPLOAD_DIR / filename

    try:
        file_path.write_bytes(content)

        # Save document metadata and chunks in SQLite.
        with get_connection() as connection:
            # Replace an existing document with the same filename.
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

        # Reload all saved chunks so memory matches SQLite.
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT d.filename, c.text
                FROM documents d
                JOIN document_chunks c ON d.id = c.document_id
                ORDER BY d.id, c.chunk_index
                """
            ).fetchall()

        chunks = [
            {"source": row["filename"], "text": row["text"]}
            for row in rows
        ]
        sources = {item["source"] for item in chunks}

        # Rebuild FAISS using all documents saved in SQLite.
        rebuild_faiss_index()

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save or index document: {error}",
        )

    return {
        "message": "Document saved to SQLite and indexed successfully!",
        "filename": filename,
        "chunks_added": len(new_chunks),
        "total_chunks": len(chunks),
        "documents": sorted(sources),
        "indexed_chunks": faiss_index.ntotal,
    }


# --------------------------------------------------
# 11. List uploaded documents
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
# 12. Chat endpoint using FAISS retrieval
# --------------------------------------------------

@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    question = request.question.strip()

    if not question:
        raise HTTPException(
            status_code=400,
            detail="Question cannot be empty.",
        )

    relevant = []
    context = ""

    print("UPLOADED SOURCES:", sorted(sources))

    if chunks and faiss_index is not None:
        question_embedding = embedding_model.encode(
            [question],
            convert_to_numpy=True,
            normalize_embeddings=True,
        )

        question_embedding = np.asarray(
            question_embedding,
            dtype=np.float32,
        )

        print("\nQUESTION:", question)
        print("TOTAL INDEXED CHUNKS:", faiss_index.ntotal)

        # Search all chunks so each document can contribute results.
        scores, indices = faiss_index.search(
            question_embedding,
            faiss_index.ntotal,
        )

        # Keep the best matching chunks from each source.
        best_by_source = {}

        for score, idx in zip(scores[0], indices[0]):
            if idx < 0:
                continue

            item = chunks[int(idx)]
            source = item["source"]
            score = float(score)

            print(f"SCORE: {score:.4f} | SOURCE: {source}")

            if score < SIMILARITY_THRESHOLD:
                continue

            if source not in best_by_source:
                best_by_source[source] = []

            best_by_source[source].append((score, item))

        # Up to 5 relevant chunks per document.
        for source, matches in best_by_source.items():
            matches.sort(key=lambda pair: pair[0], reverse=True)

            for score, item in matches[:5]:
                relevant.append(item)

        context = "\n\n".join(
            f"Source: {item['source']}\n"
            f"Excerpt: {item['text']}"
            for item in relevant
        )

        print("RETRIEVED CHUNKS:", len(relevant))
        print(
            "RETRIEVED SOURCES:",
            sorted({item["source"] for item in relevant}),
        )

    # ----------------------------------------------
    # Prepare the LLM prompt
    # ----------------------------------------------

    if context:
        user_prompt = f"""
Answer the user's question using the documentation excerpts below.

Rules:
1. Use only the excerpts for company-specific factual claims.
2. Do not invent missing policies, contact details, or user rights.
3. Distinguish company policies from product features and mission
   statements.
4. If the excerpts do not support an answer, clearly say so.
5. Cite the source filename when the excerpt supports the claim.
6. Do not claim a source supports a fact unless the excerpt does.
7. Do not use generic industry practices to fill gaps.

DOCUMENTATION EXCERPTS:
{context}

QUESTION:
{question}
"""
    else:
        user_prompt = f"""
No sufficiently similar documentation excerpts were retrieved.

Question:
{question}

Explain that the available uploaded documents did not provide
sufficient evidence to answer this company-specific question.
Do not invent company facts or policies.
"""

    # ----------------------------------------------
    # Generate the answer with Mistral
    # ----------------------------------------------

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

        # Return unique source filenames from retrieved chunks.
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
