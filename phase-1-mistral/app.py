
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pypdf import PdfReader
from sklearn.feature_extraction.text import TfidfVectorizer
from mistralai.client import Mistral

load_dotenv()

api_key = os.getenv("MISTRAL_API_KEY")
if not api_key:
    raise ValueError("MISTRAL_API_KEY missing in .env")

client = Mistral(api_key=api_key)
MODEL = "ministral-8b-2512"




SYSTEM_PROMPT = """
You are an evidence-grounded assistant for company documentation.

STRICT RULES:
1. Use only the supplied documentation excerpts for factual claims.
2. Never invent policies, email addresses, user rights, security
   controls, legal requirements, or service guarantees.
3. A source filename alone does not prove that a claim is supported.
   The actual excerpt must support the claim.
4. Do not turn product features, company mission statements,
   news reports, or marketing claims into company policies.
5. Separate explicit policies from general company information.
6. If evidence is incomplete, clearly say what is and is not specified.
7. If no excerpt supports a claim, say:
   "The provided document excerpts do not specify this."
8. Do not infer missing details from general knowledge.
9. Cite the source filename only when that source supports the claim.
10. Do not describe a policy as confirmed unless the excerpts
    explicitly support it.

For questions about company policies:
- Include only policies supported by the excerpts.
- Summarize each policy faithfully without adding new obligations.
- Put uncertain or missing details in a separate section.
- If the excerpts do not establish a policy, say so.
"""



app = FastAPI(title="Developer Documentation AI Assistant")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173",
                   "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# First-version in-memory document storage
chunks = []
sources = set()


class ChatRequest(BaseModel):
    question: str


class ChatResponse(BaseModel):
    answer: str
    sources: list[str] = []


def split_text(text, chunk_size=180, overlap=40):
    """Split text into overlapping word-based chunks."""
    words = text.split()
    result = []
    step = chunk_size - overlap

    for start in range(0, len(words), step):
        chunk = " ".join(words[start:start + chunk_size])
        if chunk:
            result.append(chunk)

    return result


def extract_text(filename, content):
    extension = Path(filename).suffix.lower()

    if extension == ".pdf":
        import io
        reader = PdfReader(io.BytesIO(content))
        return "\n".join(page.extract_text() or "" for page in reader.pages)

    if extension in {".txt", ".md"}:
        return content.decode("utf-8-sig", errors="replace")

    raise HTTPException(
        status_code=400,
        detail="Unsupported file type. Upload PDF, TXT, or MD files.",
    )


@app.get("/")
def home():
    return {"message": "Developer Documentation AI Assistant is running!"}


@app.post("/upload")
async def upload_document(file: UploadFile = File(...)):
    global chunks

    filename = file.filename or "document"
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

    # Store the source filename with every chunk
    for chunk in new_chunks:
        chunks.append({"source": filename, "text": chunk})

    sources.add(filename)

    return {
        "message": "Document uploaded successfully!",
        "filename": filename,
        "chunks_added": len(new_chunks),
        "total_chunks": len(chunks),
        "documents": sorted(sources),
    }


@app.get("/documents")
def list_documents():
    return {
        "documents": sorted(sources),
        "total_chunks": len(chunks),
    }


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    question = request.question.strip()

    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    
    context = ""

    if chunks:
        corpus = [item["text"] for item in chunks]

        vectorizer = TfidfVectorizer(
            stop_words=None,
            ngram_range=(1, 2),
            lowercase=True,
        )

        matrix = vectorizer.fit_transform(corpus)
        query_vector = vectorizer.transform([question])
        scores = (matrix @ query_vector.T).toarray().ravel()

        # Show the best matching chunks for debugging
        best_indices = scores.argsort()[::-1][:5]

        print("QUESTION:", question)
        print("MAX MATCH SCORE:", scores.max() if len(scores) else 0)

        relevant = [
            chunks[i]
            for i in best_indices
            if scores[i] > 0
        ]

        print("RETRIEVED CHUNKS:", len(relevant))

        if relevant:
            context = "\n\n".join(
                f"Source: {item['source']}\n{item['text']}"
                for item in relevant
            )

        print("RETRIEVED CONTENT:", context[:1500])
        print("QUESTION:", question)
        print("RETRIEVED CHUNKS:", len(relevant))

        for item in relevant:
            print("\nSOURCE:", item["source"])
            print("EXCERPT:", item["text"][:1000])
    
    if context:
        user_prompt = f"""
You are answering questions about uploaded company documents.

Rules:
1. Use ONLY the documentation excerpts below for company-specific facts.
2. If the user asks about company policies, identify and summarize
   policies mentioned in the excerpts.
3. If the excerpts do not contain the answer, say that the
   available documents do not provide that information.
4. Do not replace missing company-specific information with
   generic industry practices.
5. Cite the source filename when possible.
6. Interpret follow-up questions using the conversation context
   when that context is available.

DOCUMENTATION EXCERPTS:
{context}

QUESTION:
{question}
"""
    else:
        user_prompt = f"""
No relevant information was retrieved from uploaded documents.

For questions about a specific company, do not invent its address,
policies, or other details. Explain that relevant documentation
could not be found in the available uploaded files.

Question: {question}
"""


    
    try:
        response = client.chat.complete(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        )

        answer = response.choices[0].message.content

        source_list = list(dict.fromkeys(
            item["source"] for item in relevant
        )) if context else []

        return {
            "answer": str(answer),
            "sources": source_list,
        }

    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=f"AI provider request failed: {error}",
        )

