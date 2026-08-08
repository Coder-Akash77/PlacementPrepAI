import shutil

from app.services.pdf_service import extract_text_from_pdf
from app.services.chunk_service import split_text
from app.services.embedding_service import generate_embeddings
from app.services.vector_db_service import store_embeddings


def save_uploaded_file(file):

    save_path = f"uploads/{file.filename}"

    # Save PDF
    with open(save_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # Extract text
    text = extract_text_from_pdf(save_path)

    # Split text into chunks
    chunks = split_text(text)

    # Generate embeddings
    embeddings = generate_embeddings(chunks)

    # Store in ChromaDB
    store_embeddings(chunks, embeddings)

    return {
        "file_path": save_path,
        "chunks": len(chunks)
    }