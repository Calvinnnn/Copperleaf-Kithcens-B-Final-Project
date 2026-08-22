import sys
import os
import uvicorn

# إضافة مجلد admin مباشرة إلى مسار البحث لبايثون
current_dir = os.path.dirname(os.path.abspath(__file__))
admin_dir = os.path.join(current_dir, "platform", "admin")
sys.path.insert(0, admin_dir)
sys.path.insert(0, current_dir)

if __name__ == "__main__":
    # استيراد server_bridge مباشرة كملف مستقل بعد إضافة مساره للـ sys.path
    import server_bridge
    uvicorn.run(server_bridge.app, host="0.0.0.0", port=8000)