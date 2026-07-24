import asyncio
import uvicorn
import logging

# Import your components
import bot
from api_server import app

# Enable logging to see exactly who boots up
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("UnifiedLauncher")

async def main():
    import database as db
    db.init_db()

    # 1. Initialize the Telegram Bot application manually 
    # (This extracts it from its blocking run_polling routine)
    telegram_app = bot.application # or bot.app depending on what your bot.py calls it
    
    logger.info("Initializing Telegram Bot components...")
    await telegram_app.initialize()
    await telegram_app.start()
    await telegram_app.updater.start_polling()
    
    # 2. Configure Uvicorn to run on the SAME active asyncio loop
    logger.info("Configuring FastAPI Uvicorn Server on port 8000...")
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, loop="asyncio", log_level="info")
    server = uvicorn.Server(config)
    
    # 3. Serve the Web API (This keeps the entire script loop alive!)
    logger.info("🚀 System Fully Operational! Serving both layers simultaneously...")
    await server.serve()
    
    # 4. Cleanup on exit
    await telegram_app.updater.stop()
    await telegram_app.stop()
    await telegram_app.shutdown()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down cleanly...")