import asyncio
import logging
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from langchain_core.messages import HumanMessage, SystemMessage
from dotenv import load_dotenv
import os
from typing import Dict, Any, Optional, TYPE_CHECKING

from .pett_websocket_client import PettWebSocketClient

if TYPE_CHECKING:
    from .decision_engine import PetDecisionMaker

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


class PetTelegramBot:
    def __init__(
        self,
        websocket_client: Optional[PettWebSocketClient] = None,
        decision_engine: Optional["PetDecisionMaker"] = None,
        is_prod: bool = True,
    ):
        """Initialize the Telegram bot with shared WebSocket and decision engine.

        Args:
            websocket_client: Shared WebSocket client (avoids duplicate connections)
            decision_engine: Shared decision engine (avoids duplicate model initialization)
            is_prod: Production mode flag
        """
        # Store user configurations and shared components
        self.user_configs: Dict[int, Dict[str, Any]] = {}

        # Use shared components to avoid duplicates
        self.websocket_client = websocket_client
        self.engine = decision_engine
        # PetDecisionMaker doesn't have pett_tools, agent, or model attributes
        self.pett_tools = None
        self.agent = None
        self.model = None
        self.is_prod = is_prod

        # Initialize Telegram bot
        self.token = (
            os.environ.get("CONNECTION_CONFIGS_CONFIG_TELEGRAM_BOT_TOKEN") or ""
        ).strip()
        if not self.token:
            logger.warning(
                "⚠️ CONNECTION_CONFIGS_CONFIG_TELEGRAM_BOT_TOKEN not provided - Telegram bot will not be available"
            )
            self.application = None
            return

        self.application = Application.builder().token(self.token).build()
        self._setup_handlers()

        logger.info("🤖 Telegram bot initialized with shared components")

    async def _ensure_websocket_connection(self) -> bool:
        """Verify WebSocket connection is active (uses shared client)."""
        if self.websocket_client is None:
            logger.error("No WebSocket client provided to Telegram bot")
            return False

        # Check if still connected and authenticated
        if not self.websocket_client.is_connected():
            logger.warning("WebSocket connection lost")
            return False

        if not self.websocket_client.is_authenticated():
            logger.warning("WebSocket not authenticated")
            return False

        return True

    def _setup_handlers(self):
        """Setup Telegram bot message handlers."""
        # Message handler for all messages
        self.application.add_handler(MessageHandler(filters.TEXT, self.handle_message))

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle all text messages using LangChain agent."""
        user_id = update.effective_user.id
        message_text = update.message.text

        # Ensure WebSocket connection
        connected = await self._ensure_websocket_connection()
        if not connected:
            await update.message.reply_text(
                "❌ Sorry, I couldn't connect to the pet server. Please try again later."
            )
            return

        # Get user config or create new one
        if user_id not in self.user_configs:
            self.user_configs[user_id] = {
                "configurable": {"thread_id": f"user_{user_id}"}
            }

        config = self.user_configs[user_id]

        if not self.agent:
            await update.message.reply_text("❌ Not connected. Please try again.")
            return

        try:
            # Send typing indicator
            await context.bot.send_chat_action(
                chat_id=update.effective_chat.id, action="typing"
            )

            # Process message with shared LangChain agent
            response_text = await self._process_with_agent(message_text, config)

            # Send response
            await update.message.reply_text(response_text, parse_mode="Markdown")

        except Exception as e:
            logger.error(f"Error processing message: {e}")
            error_message = "Sorry, I encountered an error processing your message. Please try again!"
            await update.message.reply_text(error_message)

    async def _process_with_agent(self, message: str, config: dict) -> str:
        """Process message with LangChain agent."""
        # Create messages for the agent

        if self.websocket_client.get_pet_data():
            pet_data = self.websocket_client.get_pet_data()
        else:
            return "There is no pet data available. Please register a pet first or try again later."

        messages = [
            SystemMessage(content="The user current pet is: " + str(pet_data)),
            HumanMessage(content=message),
        ]

        # Process with agent
        response = ""
        result = await self.agent.ainvoke({"messages": messages}, config)
        if "messages" in result and result["messages"]:
            last_message = result["messages"][-1]
            if hasattr(last_message, "content"):
                response = last_message.content

        return (
            response
            if response
            else "I'm not sure how to respond to that. Try asking about the pet or telling me what you'd like to do!"
        )

    async def run(self):
        """Start the Telegram bot."""
        if not self.application:
            logger.warning("⚠️ Telegram bot not initialized - skipping startup")
            return

        logger.info("Starting PetBot with Pett.ai integration...")
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling()

        try:
            # Keep the bot running
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            logger.info("Stopping PetBot...")
            await self.application.updater.stop()
            await self.application.stop()
            await self.application.shutdown()

            # Close WebSocket connection
            if self.websocket_client:
                await self.websocket_client.disconnect()


async def main():
    """Main function to run the bot."""
    try:
        # bot = PetTelegramBot()
        # await bot.run()
        print("Telegram bot is not running")
    except Exception as e:
        logger.error(f"Error starting bot: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
