import chromadb

client = chromadb.PersistentClient(path="chroma_db")

collection = client.get_or_create_collection(
    name="resume_collection"
)


def store_embeddings(chunks, embeddings, sections):

    ids = [str(i) for i in range(len(chunks))]

    metadatas = [
        {"section": section}
        for section in sections
    ]

    collection.add(
        ids=ids,
        documents=chunks,
        embeddings=embeddings.tolist(),
        metadatas=metadatas
    )


def search_similar_chunks(query_embedding, n_results=10, section=None):

    query = {
        "query_embeddings": [query_embedding.tolist()],
        "n_results": n_results
    }

    if section:
        query["where"] = {"section": section}

    results = collection.query(**query)

    return results["documents"][0]