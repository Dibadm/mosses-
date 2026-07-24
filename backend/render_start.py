import os
import sys
import threading
import time
import uvicorn
import bot

PORT = os.environ.get("PORT", os.environ.get("API_PORT", "8000"))

def run_api():
    uvicorn.run("api_server:app", host="0.0.0.0", port=int(PORT), log_level="info")

if __name__ == "__main__":
    api_thread = threading.Thread(target=run_api, daemon=True)
    api_thread.start()
    time.sleep(2)
    bot.main()
