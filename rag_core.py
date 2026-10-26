import chromadb
from openai import OpenAI

# 1. Initialize OpenAI Client (Pointed at local Ollama) & Persistent DB
client = OpenAI(
    base_url="http://localhost:11434/v1",
    api_key="ollama"
)

DB_PATH = "./chroma_db"
chroma_client = chromadb.PersistentClient(path=DB_PATH)
collection = chroma_client.get_collection(name="sre_runbooks")

# 2. Accept live user query
user_query = input("\nEnter your SRE incident question:\n> ")

# 3. Vector Search 
# 3. Vector Search using OpenAI SDK with Distance Checking
print("\nSearching persistent ChromaDB...")
query_res = client.embeddings.create(
    input=user_query,
    model="nomic-embed-text"
)
query_vector = query_res.data[0].embedding

search_results = collection.query(
    query_embeddings=[query_vector],
    n_results=2
)

# ChromaDB distance: lower means closer match (0.0 = identical)
best_distance = search_results["distances"][0][0]

# Distance threshold check (Guardrail)
if best_distance > 0.85:
    print("\n--- RECOMMENDED SRE ACTION PLAN ---")
    print(f"No relevant runbook found in the knowledge base (Distance score: {best_distance:.2f}).")
    print("Please verify the error code or add a new runbook to ./knowledge_base/.")
    exit(0)

# 4. Generate Answer using standard OpenAI Chat Completions
print("\nSynthesizing response with TinyLlama...")
system_prompt = (
    "You are an enterprise SRE reliability agent. "
    "Answer the user's technical question strictly using only the provided runbook context. "
    "State exact commands and mitigation steps if present. "
    "Keep your answer concise, under 3 sentences, and never repeat yourself."
)

user_prompt = f"""
Context:
{context_block}

Question:
{user_query}
"""

completion = client.chat.completions.create(
    model="tinyllama",
    temperature=0.1,#to add some randomness to the output
    messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
)

print("\n--- RECOMMENDED SRE ACTION PLAN ---")
print(completion.choices[0].message.content)