import os
import glob
import chromadb
from openai import OpenAI

# 1. Initialize OpenAI Client (Pointed at local Ollama) & Persistent DB
client = OpenAI(
    base_url="http://localhost:11434/v1",
    api_key="ollama"
)

DB_PATH = "./chroma_db"
chroma_client = chromadb.PersistentClient(path=DB_PATH)

try:
    chroma_client.delete_collection(name="sre_runbooks")
except Exception:
    pass

collection = chroma_client.create_collection(name="sre_runbooks")

# 2. Ingestion Loop
DOCS_DIR = "./knowledge_base"
doc_files = glob.glob(os.path.join(DOCS_DIR, "*.md"))

chunk_id_counter = 0

for filepath in doc_files:
    filename = os.path.basename(filepath)
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    raw_chunks = [c.strip() for c in content.split("\n\n") if len(c.strip()) > 40]

    for chunk in raw_chunks:
        # Use OpenAI SDK to generate local embeddings
        response = client.embeddings.create(
            input=chunk,
            model="nomic-embed-text"
        )
        vector = response.data[0].embedding

        collection.add(
            documents=[chunk],
            embeddings=[vector],
            metadatas=[{"source": filename}],
            ids=[f"chunk_{chunk_id_counter}"]
        )
        chunk_id_counter += 1

print(f"Successfully ingested {chunk_id_counter} chunks into {DB_PATH}.")