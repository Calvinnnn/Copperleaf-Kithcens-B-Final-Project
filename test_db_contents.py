import sys
import os

sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from rag.vector_store import get_collection, query_vector_store

print("==================================================")
print("🔍 CHECKING CHROMADB COLLECTION & CONTENTS")
print("==================================================")

try:
    collection = get_collection()
    count = collection.count()
    print(f"📊 Total Chunk Documents in DB: {count}")

    if count == 0:
        print("\n⚠️ RESULT: Vector Database is EMPTY! You need to run the ingestion script to load documents into ChromaDB.")
    else:
        print("\n🔍 Testing Direct Raw Query (Top 3):")
        # تجربة استعلام مباشر بدون تصفية
        raw_results = query_vector_store(query_text="food safety temperature", top_k=3)
        print(f"Retrieved {len(raw_results)} results directly from ChromaDB:")
        for idx, item in enumerate(raw_results, 1):
            print(f"\n--- Chunk {idx} ---")
            print(f"Text Sample: {str(item)[:200]}...")

except Exception as e:
    print(f"❌ Error checking DB: {e}")