import sys
import os

# إضافة المجلد الرئيسي للـ Path
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from rag.agentic_rag import AgenticRAGOrchestrator

def run_rag_tests():
    print("==================================================")
    print("   🔍 STARTING RAG & DATABASE COMPREHENSIVE TESTS  ")
    print("==================================================\n")

    try:
        orchestrator = AgenticRAGOrchestrator()
        print("✅ Success: AgenticRAGOrchestrator initialized successfully.")
    except Exception as e:
        print(f"❌ FAIL: Orchestrator Initialization Error -> {e}")
        return

    # قائمة الحالات الممكنة لاختبار قاعدة البيانات
    test_cases = [
        {
            "name": "Case 1: Standard Query (Normal DB Hit)",
            "query": "What are the food safety standards for cold storage temperature?"
        },
        {
            "name": "Case 2: Irrelevant / Out-of-Bound Query (No DB Hit)",
            "query": "How to fix a puncture on a space shuttle tire?"
        },
        {
            "name": "Case 3: Empty String Query",
            "query": ""
        },
        {
            "name": "Case 4: Special Characters & Injection Test",
            "query": "'; DROP TABLE documents; -- SELECT * FROM '*' WHERE 1=1"
        },
        {
            "name": "Case 5: Very Long Query (Edge Case)",
            "query": "policy " * 200
        }
    ]

    for test in test_cases:
        print(f"\n--------------------------------------------------")
        print(f"🧪 Running: {test['name']}")
        print(f"   Query: {test['query'][:60]}..." if len(test['query']) > 60 else f"   Query: {test['query']}")
        
        try:
            result = orchestrator.run(query=test['query'])
            
            # فحص شكل الناتج والأخطاء الممكنة
            context = getattr(result, "answer_context", None)
            
            print("   Response Object Attributes:", dir(result))
            print(f"   Context Retrieved Length: {len(str(context)) if context else 0} chars")
            print(f"   Context Sample: {str(context)[:150]}...")
            print(f"✅ PASSED: {test['name']}")
            
        except AttributeError as ae:
            print(f"❌ FAIL (AttributeError): Missing required attribute in response -> {ae}")
        except KeyError as ke:
            print(f"❌ FAIL (KeyError): Missing expected key in DB results -> {ke}")
        except Exception as e:
            print(f"❌ FAIL (General Error): Type: {type(e).__name__} | Message: {e}")

    print("\n==================================================")
    print("   🏁 ALL TESTS COMPLETED")
    print("==================================================")

if __name__ == "__main__":
    run_rag_tests()