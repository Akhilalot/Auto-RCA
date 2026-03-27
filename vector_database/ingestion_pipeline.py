import json
import logging
import os

import chromadb
from chromadb.utils import embedding_functions
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Configuration
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_FILE = os.path.join(SCRIPT_DIR, "..", "FAQs", "resolution_faqs.json")
DB_PATH = os.path.join(SCRIPT_DIR, "chroma_db")
COLLECTION_NAME = os.getenv("CHROMA_COLLECTION_NAME", "sre_runbooks")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-large")


def build_vector_db() -> None:
    logger.info("Loading FAQs from %s", JSON_FILE)

    if not os.path.exists(JSON_FILE):
        raise FileNotFoundError(f"Could not find {JSON_FILE}. Run the FAQ generator script first.")

    with open(JSON_FILE, "r", encoding="utf-8") as f:
        faqs = json.load(f)

    # Prepare the data for ChromaDB
    documents = []
    metadatas = []
    ids = []

    for faq in faqs:
        content = f"Category: {faq['category']}\nQuestion: {faq['question']}\nResolution: {faq['answer']}"
        documents.append(content)
        metadatas.append({
            "category": faq["category"],
            "faq_id": faq["faq_id"],
        })
        ids.append(faq["faq_id"])

    logger.info("Prepared %d documents for indexing", len(documents))

    # Initialize ChromaDB Persistent Client
    logger.info("Initializing ChromaDB persistent client at %s", DB_PATH)
    client = chromadb.PersistentClient(path=DB_PATH)

    # Define the OpenAI Embedding Function
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise EnvironmentError("OPENAI_API_KEY is not set")

    logger.info("Using OpenAI embedding model: %s", EMBEDDING_MODEL)
    openai_ef = embedding_functions.OpenAIEmbeddingFunction(
        api_key=api_key,
        model_name=EMBEDDING_MODEL,
    )

    # Create (or get) the collection
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=openai_ef,
        metadata={"hnsw:space": "cosine"},
    )

    # Upsert data into the database
    logger.info("Embedding and indexing documents into ChromaDB ...")
    collection.upsert(
        documents=documents,
        metadatas=metadatas,
        ids=ids,
    )
    logger.info("Successfully built and persisted the ChromaDB vector index")

    # Test the RAG search
    logger.info("--- Testing vector search ---")
    agent_query = (
        "We had a deployment and now processPayment is failing "
        "with high payment gateway latency and PaymentGatewayTimeoutException."
    )
    logger.info("Agent Query: %s", agent_query)

    results = collection.query(query_texts=[agent_query], n_results=1)

    if results["documents"][0]:
        best_match = results["documents"][0][0]
        match_id = results["ids"][0][0]
        distance = results["distances"][0][0]
        logger.info("Top result: %s (distance=%.4f)", match_id, distance)
        logger.info("Content: %s", best_match[:200])
    else:
        logger.warning("No results found for test query")

if __name__ == "__main__":
    build_vector_db()