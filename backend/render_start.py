import os
import sys
import threading
import uvicorn
import bot

PORT = os.environ.get("PORT", os.environ.get("API_PORT", "8000"))

def run_bot():
    bot.main()

def run_api():
    uvicorn.run("api_server:app", host="0.0.0.0", port=int(PORT), log_level="info")

if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    uvicorn.run("api_server:app", host="0.0.0.0", port=int(PORT), log_level="info")
