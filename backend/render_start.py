import os
import subprocess
import sys
import bot

PORT = os.environ.get("PORT", os.environ.get("API_PORT", "8000"))

if __name__ == "__main__":
    api = subprocess.Popen([
        sys.executable, "-m", "uvicorn",
        "api_server:app",
        "--host", "0.0.0.0",
        "--port", PORT,
        "--log-level", "info",
    ])
    try:
        bot.main()
    finally:
        api.terminate()
