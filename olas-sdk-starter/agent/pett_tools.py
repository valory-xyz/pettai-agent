from typing import Dict, Any, Optional, List, Annotated, TYPE_CHECKING
import asyncio
from .pett_websocket_client import PettWebSocketClient
import logging
import json
import random
from langchain_core.tools import BaseTool, tool
from langchain_core.tools import InjectedToolArg

if TYPE_CHECKING:
    _InjectedClientBase = PettWebSocketClient
else:  # pragma: no cover - runtime only cares about injection marker
    _InjectedClientBase = Any

InjectedClientArg = Annotated[_InjectedClientBase, InjectedToolArg]

logger = logging.getLogger(__name__)

CONSUMABLES = [
    "BURGER",
    "SALAD",
    "STEAK",
    "COOKIE",
    "PIZZA",
    "SUSHI",
    "ENERGIZER",
    "POTION",
    "XP_POTION",
    "SUPER_XP_POTION",
    "SMALL_POTION",
    "LARGE_POTION",
    "REVIVE_POTION",
    "POISONOUS_ARROW",
    "REINFORCED_SHIELD",
    "BATTLE_SWORD",
    "ACCOUNTANT",
]

ACCESSORIES = [
    "CROWN",
    "HALO",
    "DEVIL_HORNS",
    "UNICORN_HORN",
    "PARTY_HAT",
    "MUSHROOMS",
    "STEM",
    "BEANIE_BEIJE",
    "CAP_GREEN",
    "SAMURAI_HELMET",
    "BALLOON_ETH",
    "BALLOON_BASE",
    "BALLOON_BTC",
    "KITE_BLUE",
    "RACKET_PADEL",
    "BALLOON_RED",
    "WINGS_ANGEL",
    "WINGS_DEVIL",
    "WINGS_FAIRY",
    "WINGS_BAT",
    "TOY_BULL",
    "TOY_BEAR",
    "TOY_FROG",
    "TOY_CRAB",
    "WORLD_ID",
    "CAP_DS",
    "HALLOWEEN",
    "IVAN_ON_TECH",
    "BEANIE_MOCHI",
    "CAP_PAAL",
    "BEANIE_DIAMOND",
    "HAT_AFRICA",
    "BEANIE_NEIRO",
    "HAT_CHINA",
    "GOGGLES_MILITARY",
    "HAT_ELF",
    "HAT_SANTA",
    "HAT_THANKSGIVING",
    "PARTY_HAT_NEW_YEARS",
    "VEST_PATAGONIA",
    "ROBE_SECRET",
]

BASE_ACTIONS = [
    "RUB",
    "SHOWER",
    "SLEEP",
    "CONSUMABLES_USE",
    "CONSUMABLES_BUY",
    "CONSUMABLES_GET",
    "KITCHEN_GET",
    "MALL_GET",
    "CLOSET_GET",
    "ACCESSORY_USE",
    "ACCESSORY_BUY",
    "THROWBALL",
    "AI_SEARCH",
    "PERSONALITY_GET",
    "GEN_IMAGE",
    "HOTEL_CHECK_IN",
    "HOTEL_CHECK_OUT",
    "HOTEL_BUY",
    "OFFICE_GET",
    "WITHDRAWAL_CREATE",
    "WITHDRAWAL_QUEUE",
    "WITHDRAWAL_JUMP",
    "WITHDRAWAL_USE",
    "WITHDRAWAL_DATA",
    "WITHDRAWAL_QUEUE_DATA",
    "TRANSFER",
    "DEPOSIT",
    "REFERRAL_GET",
    "REFERRAL_USE",
    "QUEST_GET",
    "QUEST_USE",
    "ACHIEVEMENTS_GET",
    "ACHIEVEMENTS_USE",
    "LEADERBOARD_STATS_GET",
    "GUILD_UPDATE_INFO",
    "GUILD_JOIN_REQUESTS_GET",
    "GUILD_HANDLE_JOIN_REQUEST",
    "GUILD_INVITE_CREATE",
    "GUILD_INVITE_RESPOND",
    "GUILD_ACCESS_TYPE_CHANGE",
    "GUILD_MEMBER_ROLE_CHANGE",
    "GUILD_JOIN",
    "GUILD_MEMBER_KICK",
    "GUILD_LEADERSHIP_TRANSFER",
    "GUILD_UPGRADE_BUY",
    "GUILD_UPGRADE_COSTS",
    "GUILD_GET",
    "GUILD_SELF_GET",
    "GUILD_TOP_GET",
    "GUILD_VAULT_TOKENS_DEPOSIT",
    "GUILD_CREATE_COST",
    "GUILD_CREATE",
    "GUILD_SEARCH",
    "GUILD_LEAVE",
    "STAKING_GET",
    "STAKING_CREATE",
    "STAKING_CLAIM",
    "PLAY_SLOTS",
    "PLAY_DICE",
    "PLAY_POKER",
    "GET_POKER_STATE",
    "CANCEL_POKER",
    "PLAY_KOTH",
    "GET_KOTH_LEADERBOARD",
    "PLAY_DOORS",
]


class PettTools:
    def __init__(self, websocket_client: PettWebSocketClient):
        self.client = websocket_client

    def set_client(self, websocket_client: PettWebSocketClient) -> None:
        """Set the WebSocket client for this instance."""
        self.client = websocket_client

    def _validate_client(self) -> bool:
        """Validate that the client is available and connected."""
        if not self.client:
            logger.error("WebSocket client not set")
            return False
        if not self.client.is_connected():
            logger.error("WebSocket client not connected")
            return False
        return True

    def _run_async(self, coro) -> Any:
        """Helper method to run async functions in sync context."""
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        return loop.run_until_complete(coro)

    def _escape_for_telegram(self, text: str) -> str:
        """Escape text for Telegram Markdown formatting."""
        # Characters that need escaping in Markdown
        escape_chars = [
            "_",
            "*",
            "[",
            "]",
            "(",
            ")",
            "~",
            "`",
            ">",
            "#",
            "+",
            "-",
            "=",
            "|",
            "{",
            "}",
            ".",
            "!",
        ]

        escaped_text = text
        for char in escape_chars:
            escaped_text = escaped_text.replace(char, f"\\{char}")

        return escaped_text

    def get_pet_status(self) -> str:
        """Get the current status and statistics of the pet.

        Retrieves comprehensive information about the pet's current state,
        including health, happiness, energy levels, and other vital statistics.
        This is useful for monitoring your pet's well-being and making informed
        care decisions.

        Returns:
            str: Formatted pet status information, or error message if retrieval fails.
        """
        if not self._validate_client():
            return "❌ WebSocket client not available or connected."

        try:
            # logger.info("[PetTools] Getting pet status and statistics")
            if self.client is None:
                return "❌ WebSocket client is None."

            # At this point, self.client is guaranteed to be not None
            client = self.client
            pet_data = client.get_pet_data()
            if pet_data:
                # logger.info("[TOOL] Successfully retrieved pet status data")
                return (
                    f"🐾 Pet Status:\n{self._escape_for_telegram(json.dumps(pet_data))}"
                )
            else:
                logger.warning("[TOOL] No pet data available from client")
                return "❌ No pet data available."
        except Exception as e:
            logger.error(f"[TOOL] Error getting pet status: {e}")
            return f"❌ Error getting pet status: {str(e)}"

    def create_tools(self) -> List[BaseTool]:
        """Create tool functions that are bound to this instance."""

        @tool
        def rub_pet(
            client: InjectedClientArg = None,
        ) -> str:
            """Rub the pet to increase happiness and strengthen your bond.

            This is one of the most basic and important pet care actions. Rubbing your pet
            will increase their happiness level, which affects their overall well-being and
            performance in various activities. Regular rubbing helps maintain a strong
            emotional connection with your pet.

            Returns:
                str: Success message if the pet was rubbed successfully, error message otherwise.
            """
            logger.info("[TOOL] Attempting to rub pet")

            if not client:
                client = self.client

            if not client or not client.is_connected():
                logger.error(
                    "[TOOL] WebSocket client not available or connected for rub_pet"
                )
                return "❌ WebSocket client not available or connected."

            try:
                success = self._run_async(client.rub_pet())
                if success:
                    logger.info("[TOOL] Successfully rubbed pet")
                    return "🤗 Pet loves the rubs! Happiness increased."
                else:
                    logger.warning("[TOOL] Failed to rub pet")
                    return "❌ Failed to rub pet."
            except Exception as e:
                logger.error(f"[TOOL] Error rubbing pet: {e}")
                return f"❌ Error rubbing pet: {str(e)}"

        @tool
        def shower_pet(
            client: InjectedClientArg = None,
        ) -> str:
            """Give the pet a refreshing shower to clean and revitalize them.

            Showering your pet is essential for maintaining their hygiene and health.
            A clean pet is a happy pet! This action will improve your pet's cleanliness
            status and may also provide a small boost to their overall well-being.
            Regular showers help prevent illness and keep your pet looking their best.

            Returns:
                str: Success message if the pet was showered successfully, error message otherwise.
            """
            logger.info("[TOOL] Attempting to shower pet")

            if not client:
                client = self.client

            if not client or not client.is_connected():
                logger.error(
                    "[TOOL] WebSocket client not available or connected for shower_pet"
                )
                return "❌ WebSocket client not available or connected."

            try:
                success = self._run_async(client.shower_pet())
                if success:
                    logger.info("[TOOL] Successfully showered pet")
                    return "🚿 Pet is now clean and refreshed!"
                else:
                    logger.warning("[TOOL] Failed to shower pet")
                    return "❌ Failed to shower pet."
            except Exception as e:
                logger.error(f"[TOOL] Error showering pet: {e}")
                return f"❌ Error showering pet: {str(e)}"

        @tool
        def sleep_pet(
            client: InjectedClientArg = None,
        ) -> str:
            """Put the pet to sleep to restore their energy and promote healthy rest.

            Sleep is crucial for your pet's health and energy levels. When your pet sleeps,
            they will gradually restore their energy, which is needed for various activities
            and interactions. A well-rested pet is more active, happier, and performs better
            in games and challenges. Make sure your pet gets adequate rest regularly.

            Returns:
                str: Success message if the pet was put to sleep successfully, error message otherwise.
            """
            logger.info("[TOOL] Attempting to put pet to sleep")

            if not client:
                client = self.client

            if not client or not client.is_connected():
                logger.error(
                    "[TOOL] WebSocket client not available or connected for sleep_pet"
                )
                return "❌ WebSocket client not available or connected."

            try:
                success = self._run_async(client.sleep_pet())
                if success:
                    logger.info("[TOOL] Successfully put pet to sleep")
                    return "😴 Pet is now sleeping and restoring energy."
                else:
                    logger.warning("[TOOL] Failed to put pet to sleep")
                    return "❌ Failed to put pet to sleep."
            except Exception as e:
                logger.error(f"[TOOL] Error putting pet to sleep: {e}")
                return f"❌ Error putting pet to sleep: {str(e)}"

        @tool
        def throw_ball(
            client: InjectedClientArg = None,
        ) -> str:
            """Throw a ball for the pet to play with and exercise.

            Playing with a ball is an excellent way to keep your pet active and entertained.
            This interactive activity helps maintain your pet's physical fitness, provides
            mental stimulation, and strengthens the bond between you and your pet. Regular
            play sessions contribute to your pet's overall happiness and well-being.

            Returns:
                str: Success message if the ball was thrown successfully, error message otherwise.
            """
            logger.info("[TOOL] Attempting to throw ball for pet")

            if not client:
                client = self.client

            if not client or not client.is_connected():
                logger.error(
                    "[TOOL] WebSocket client not available or connected for throw_ball"
                )
                return "❌ WebSocket client not available or connected."

            try:
                success = self._run_async(client.throw_ball())
                if success:
                    logger.info("[TOOL] Successfully threw ball for pet")
                    return "🎾 Pet is playing with the ball!"
                else:
                    logger.warning("[TOOL] Failed to throw ball for pet")
                    return "❌ Failed to throw ball."
            except Exception as e:
                logger.error(f"[TOOL] Error throwing ball: {e}")
                return f"❌ Error throwing ball: {str(e)}"

        @tool
        def use_consumable(
            consumable_id: str,
            client: InjectedClientArg = None,
        ) -> str:
            """Use a consumable item on the pet to provide various benefits.

            Consumables are items that can be used to improve your pet's stats, health,
            or provide special effects. Different consumables have different effects:
            - Food items (BURGER, SALAD, STEAK, etc.) restore hunger and provide energy
            - Potions (POTION, XP_POTION, etc.) provide various stat boosts and effects
            - Special items (ENERGIZER, ACCOUNTANT, etc.) have unique beneficial effects

            Args:
                consumable_id: The ID of the consumable to use. Must be one of: "BURGER", "SALAD",
                              "STEAK", "COOKIE", "PIZZA", "SUSHI", "ENERGIZER", "POTION", "XP_POTION",
                              "SUPER_XP_POTION", "SMALL_POTION", "LARGE_POTION", "REVIVE_POTION",
                              "POISONOUS_ARROW", "REINFORCED_SHIELD", "BATTLE_SWORD", "ACCOUNTANT"

            Returns:
                str: Success message if the consumable was used successfully, error message otherwise.
            """
            logger.info(f"[TOOL] Attempting to use consumable: {consumable_id}")

            if not client:
                client = self.client

            if not client or not client.is_connected():
                logger.error(
                    "[TOOL] WebSocket client not available or connected for use_consumable"
                )
                return "❌ WebSocket client not available or connected."

            consumable_id = (consumable_id or "").strip().strip('"').strip("'")
            if consumable_id not in CONSUMABLES:
                logger.error(f"[TOOL] Invalid consumable ID provided: {consumable_id}")
                return f"❌ Invalid consumable ID: {consumable_id}. Allowed values: {', '.join(sorted(CONSUMABLES))}"

            logger.info(f"Using consumable: {consumable_id}")

            try:
                success = self._run_async(client.use_consumable(consumable_id))
                if success:
                    logger.info(f"[TOOL] Successfully used consumable: {consumable_id}")
                    return f"🍖 Used {consumable_id} on pet!"
                else:
                    logger.warning(f"[TOOL] Failed to use consumable: {consumable_id}")
                    return f"❌ Failed to use {consumable_id}."
            except Exception as e:
                logger.error(f"[TOOL] Error using consumable {consumable_id}: {e}")
                return f"❌ Error using consumable: {str(e)}"

        @tool
        def buy_consumable(
            consumable_id: str,
            amount: int = 1,
            client: InjectedClientArg = None,
        ) -> str:
            """Purchase consumable items for your pet from the store.

            This tool allows you to buy consumable items that can be used to care for your pet.
            You can purchase food items, potions, and special items in various quantities.
            Make sure you have enough currency to complete the purchase. Buying in bulk
            can be more efficient for frequently used items.

            Args:
                consumable_id: The ID of the consumable to buy. Must be one of: "BURGER", "SALAD",
                              "STEAK", "COOKIE", "PIZZA", "SUSHI", "ENERGIZER", "POTION", "XP_POTION",
                              "SUPER_XP_POTION", "SMALL_POTION", "LARGE_POTION", "REVIVE_POTION",
                              "POISONOUS_ARROW", "REINFORCED_SHIELD", "BATTLE_SWORD", "ACCOUNTANT"
                amount: The number of consumables to buy (default: 1). Must be greater than 0.

            Returns:
                str: Success message if the consumable was purchased successfully, error message otherwise.
            """
            logger.info(f"[TOOL] Attempting to buy {amount} {consumable_id}")

            if not client:
                client = self.client

            if not client or not client.is_connected():
                logger.error(
                    "[TOOL] WebSocket client not available or connected for buy_consumable"
                )
                return "❌ WebSocket client not available or connected."

            consumable_id = (consumable_id or "").strip().strip('"').strip("'")
            if consumable_id not in CONSUMABLES:
                logger.error(f"[TOOL] Invalid consumable ID provided: {consumable_id}")
                return f"❌ Invalid consumable ID: {consumable_id}. Allowed values: {', '.join(sorted(CONSUMABLES))}"

            if amount <= 0:
                logger.error(f"[TOOL] Invalid amount provided: {amount}")
                return "❌ Amount must be greater than 0."

            logger.info(f"Buying {amount} {consumable_id} for pet")

            try:
                success = self._run_async(client.buy_consumable(consumable_id, amount))
                if success:
                    logger.info(f"[TOOL] Successfully bought {amount} {consumable_id}")
                    return f"🛒 Bought {amount} {consumable_id} for pet!"
                else:
                    logger.warning(f"[TOOL] Failed to buy {amount} {consumable_id}")
                    return f"❌ Failed to buy {consumable_id} for pet"
            except Exception as e:
                logger.error(f"[TOOL] Error buying consumable {consumable_id}: {e}")
                return f"❌ Error buying consumable: {str(e)}"

        @tool
        def get_consumables(
            client: InjectedClientArg = None,
        ) -> str:
            """Retrieve the current inventory of consumable items owned by the pet.

            This tool provides a comprehensive overview of all consumable items currently
            in your pet's inventory. The response will include food items, potions, and
            special consumables along with their quantities. This information is essential
            for managing your pet's resources and planning future purchases or usage.

            Returns:
                str: Success message indicating the request was sent, error message if failed.
                     The actual inventory data will be received through the WebSocket connection.
            """
            logger.info("[TOOL] Attempting to get consumables inventory")

            if not client:
                client = self.client

            if not client or not client.is_connected():
                logger.error(
                    "[TOOL] WebSocket client not available or connected for get_consumables"
                )
                return "❌ WebSocket client not available or connected."

            try:
                success = self._run_async(client.get_consumables())
                if success:
                    logger.info("[TOOL] Successfully requested consumables list")
                    return "📋 Requested consumables list. Check the response for available items."
                else:
                    logger.warning("[TOOL] Failed to get consumables")
                    return "❌ Failed to get consumables."
            except Exception as e:
                logger.error(f"[TOOL] Error getting consumables: {e}")
                return f"❌ Error getting consumables: {str(e)}"

        @tool
        def get_kitchen(
            client: InjectedClientArg = None,
        ) -> str:
            """Retrieve kitchen information and available food preparation options.

            The kitchen is where you can prepare and manage food for your pet. This tool
            provides information about available recipes, cooking options, and current
            kitchen status. Understanding your kitchen capabilities helps you make better
            decisions about feeding your pet and managing food resources efficiently.

            Returns:
                str: Kitchen information and available options, or error message if failed.
            """
            logger.info("[TOOL] Attempting to get kitchen information")

            if not client:
                client = self.client

            if not client or not client.is_connected():
                logger.error(
                    "[TOOL] WebSocket client not available or connected for get_kitchen"
                )
                return "❌ WebSocket client not available or connected."

            try:
                logger.info("[TOOL] Getting kitchen information")
                result = self._run_async(client.get_kitchen_data(timeout=10))

                if result and not result.startswith("❌"):
                    logger.info("[TOOL] Successfully retrieved kitchen information")
                    return (
                        f"🍽️ Kitchen Information:\n{self._escape_for_telegram(result)}"
                    )
                else:
                    logger.warning(
                        f"[TOOL] Failed to get kitchen information: {result}"
                    )
                    return f"❌ Failed to get kitchen information: {result}"

            except Exception as e:
                logger.error(f"[TOOL] Error getting kitchen: {e}")
                return f"❌ Error getting kitchen: {str(e)}"

        @tool
        def get_mall(
            client: InjectedClientArg = None,
        ) -> str:
            """Retrieve mall information and browse available items for purchase.

            The mall is your one-stop shopping destination for pet care items. This tool
            provides access to the current mall inventory, including consumables, accessories,
            and special items. You can view prices, availability, and item descriptions to
            make informed purchasing decisions for your pet's needs.

            Returns:
                str: Mall information with available items and prices, or error message if failed.
            """
            logger.info("[TOOL] Attempting to get mall information")

            if not client:
                client = self.client

            if not client or not client.is_connected():
                logger.error(
                    "[TOOL] WebSocket client not available or connected for get_mall"
                )
                return "❌ WebSocket client not available or connected."

            try:
                logger.info("[TOOL] Getting mall information")
                result = self._run_async(client.get_mall_data(timeout=10))

                if result and not result.startswith("❌"):
                    logger.info("[TOOL] Successfully retrieved mall information")
                    return f"🛍️ Mall Information:\n{self._escape_for_telegram(result)}"
                else:
                    logger.warning(f"[TOOL] Failed to get mall information: {result}")
                    return f"❌ Failed to get mall information: {result}"

            except Exception as e:
                logger.error(f"[TOOL] Error getting mall: {e}")
                return f"❌ Error getting mall: {str(e)}"

        @tool
        def get_closet(
            client: InjectedClientArg = None,
        ) -> str:
            """Retrieve closet information and view available accessories and clothing.

            The closet contains all the accessories and clothing items that your pet owns.
            This tool provides an overview of your pet's wardrobe, including hats, wings,
            toys, and other decorative items. You can see which accessories are available
            for use and manage your pet's appearance and style options.

            Returns:
                str: Closet information with available accessories, or error message if failed.
            """
            logger.info("[TOOL] Attempting to get closet information")

            if not client:
                client = self.client

            if not client or not client.is_connected():
                logger.error(
                    "[TOOL] WebSocket client not available or connected for get_closet"
                )
                return "❌ WebSocket client not available or connected."

            try:
                logger.info("[TOOL] Getting closet information")
                result = self._run_async(client.get_closet_data(timeout=10))

                if result and not result.startswith("❌"):
                    logger.info("[TOOL] Successfully retrieved closet information")
                    return (
                        f"👕 Closet Information:\n{self._escape_for_telegram(result)}"
                    )
                else:
                    logger.warning(f"[TOOL] Failed to get closet information: {result}")
                    return f"❌ Failed to get closet information: {result}"

            except Exception as e:
                logger.error(f"[TOOL] Error getting closet: {e}")
                return f"❌ Error getting closet: {str(e)}"

        @tool
        def use_accessory(
            accessory_id: str,
            client: InjectedClientArg = None,
        ) -> str:
            """Equip an accessory on your pet to enhance their appearance and style.

            Accessories are cosmetic items that make your pet look unique and stylish.
            Different accessories include hats, wings, toys, and special items that can
            be equipped to customize your pet's appearance. Some accessories may also
            provide minor stat bonuses or special effects beyond just visual appeal.

            Args:
                accessory_id: The ID of the accessory to equip. Must be one of the valid
                             accessory IDs from the available collection including crowns,
                             hats, wings, toys, and other decorative items.

            Returns:
                str: Success message if the accessory was equipped successfully, error message otherwise.
            """
            logger.info(f"[TOOL] Attempting to use accessory: {accessory_id}")

            if not client:
                client = self.client

            if not client or not client.is_connected():
                logger.error(
                    "[TOOL] WebSocket client not available or connected for use_accessory"
                )
                return "❌ WebSocket client not available or connected."

            if accessory_id not in ACCESSORIES:
                logger.error(f"[TOOL] Invalid accessory ID provided: {accessory_id}")
                return f"❌ Invalid accessory ID: {accessory_id}. Allowed values: {', '.join(sorted(ACCESSORIES))}"

            try:
                success = self._run_async(client.use_accessory(accessory_id))
                if success:
                    logger.info(f"[TOOL] Successfully used accessory: {accessory_id}")
                    return f"👑 Used {accessory_id} on pet!"
                else:
                    logger.warning(f"[TOOL] Failed to use accessory: {accessory_id}")
                    return f"❌ Failed to use {accessory_id}."
            except Exception as e:
                logger.error(f"[TOOL] Error using accessory {accessory_id}: {e}")
                return f"❌ Error using accessory: {str(e)}"

        @tool
        def buy_accessory(
            accessory_id: str,
            client: InjectedClientArg = None,
        ) -> str:
            """Purchase an accessory for your pet from the store.

            This tool allows you to buy accessories that can be used to customize your
            pet's appearance. Accessories range from simple hats and crowns to elaborate
            wings and special themed items. Each accessory has its own price and may
            have limited availability. Building a diverse accessory collection allows
            for greater customization options.

            Args:
                accessory_id: The ID of the accessory to purchase. Must be one of the valid
                             accessory IDs from the store's available collection.

            Returns:
                str: Success message if the accessory was purchased successfully, error message otherwise.
            """
            logger.info(f"[TOOL] Attempting to buy accessory: {accessory_id}")

            if not client:
                client = self.client

            if not client or not client.is_connected():
                logger.error(
                    "[TOOL] WebSocket client not available or connected for buy_accessory"
                )
                return "❌ WebSocket client not available or connected."

            if accessory_id not in ACCESSORIES:
                logger.error(f"[TOOL] Invalid accessory ID provided: {accessory_id}")
                return f"❌ Invalid accessory ID: {accessory_id}. Allowed values: {', '.join(sorted(ACCESSORIES))}"

            try:
                success = self._run_async(client.buy_accessory(accessory_id))
                if success:
                    logger.info(f"[TOOL] Successfully bought accessory: {accessory_id}")
                    return f"🛒 Bought {accessory_id} for pet!"
                else:
                    logger.warning(f"[TOOL] Failed to buy accessory: {accessory_id}")
                    return f"❌ Failed to buy {accessory_id}."
            except Exception as e:
                logger.error(f"[TOOL] Error buying accessory {accessory_id}: {e}")
                return f"❌ Error buying accessory: {str(e)}"

        @tool
        def ai_search(
            prompt: str, client: InjectedClientArg = None
        ) -> str:
            """Perform an AI-powered web search to find information on any topic.

            This powerful tool leverages artificial intelligence to search the web and
            provide relevant, up-to-date information on virtually any topic. The AI
            search can help answer questions, find facts, research topics, and provide
            insights that can be useful for pet care or general knowledge. The search
            process may take up to 30 seconds to complete as it thoroughly analyzes
            web content to provide the most relevant results.

            Args:
                prompt: The search query or question to research. Be specific and clear
                       for the best results. Examples: "best food for virtual pets",
                       "how to increase pet happiness", "latest pet care trends"

            Returns:
                str: Search results with relevant information, or error message if the search failed.
            """
            logger.info(f"[TOOL] Attempting AI search with prompt: {prompt}")

            if not client:
                client = self.client

            if not client or not client.is_connected():
                logger.error(
                    "[TOOL] WebSocket client not available or connected for ai_search"
                )
                return "❌ WebSocket client not available or connected."

            if not prompt or not prompt.strip():
                logger.error("[TOOL] Empty search prompt provided")
                return "❌ Please provide a search prompt."

            try:
                logger.info(f"[TOOL] Starting AI search for: {prompt}")
                result = self._run_async(client.ai_search(prompt.strip()))

                if result and not result.startswith("❌"):
                    logger.info("[TOOL] AI search completed successfully")
                    return result
                else:
                    logger.warning(f"[TOOL] AI search failed: {result}")
                    return f"❌ AI search failed: {result}"

            except Exception as e:
                logger.error(f"[TOOL] Error during AI search: {e}")
                return f"❌ Error performing AI search: {str(e)}"

        @tool
        def get_personality(
            client: InjectedClientArg = None,
        ) -> str:
            """Retrieve detailed personality information and traits of your pet.

            Every pet has a unique personality that affects their behavior, preferences,
            and interactions. This tool provides insights into your pet's personality
            traits, behavioral patterns, and characteristics. Understanding your pet's
            personality helps you make better care decisions and build a stronger
            relationship with them.

            Returns:
                str: Success message indicating the request was sent, error message if failed.
                     The personality data will be received through the WebSocket connection.
            """
            logger.info("[TOOL] Attempting to get pet personality information")

            if not client:
                client = self.client

            if not client or not client.is_connected():
                logger.error(
                    "[TOOL] WebSocket client not available or connected for get_personality"
                )
                return "❌ WebSocket client not available or connected."

            try:
                success = self._run_async(client.get_personality())
                if success:
                    logger.info(
                        "[TOOL] Successfully requested pet personality information"
                    )
                    return "🧠 Requested pet personality information."
                else:
                    logger.warning("[TOOL] Failed to get personality information")
                    return "❌ Failed to get personality information."
            except Exception as e:
                logger.error(f"[TOOL] Error getting personality: {e}")
                return f"❌ Error getting personality: {str(e)}"

        @tool
        def generate_image(
            prompt: str, client: InjectedClientArg = None
        ) -> str:
            """Generate a custom image using AI based on your description.

            This creative tool uses artificial intelligence to generate unique images
            based on your text description. You can create artwork, scenes, or any
            visual content by describing what you want to see. The AI will interpret
            your prompt and create an original image. This is perfect for creating
            custom artwork related to your pet or any other creative project.

            Args:
                prompt: A detailed description of the image you want to generate.
                       Be specific about colors, style, objects, and composition for
                       better results. Example: "a cute cartoon pet playing in a garden"

            Returns:
                str: Success message indicating image generation started, error message if failed.
                     The generated image will be delivered through the WebSocket connection.
            """
            logger.info(f"[TOOL] Attempting to generate image with prompt: {prompt}")

            if not client:
                client = self.client

            if not client or not client.is_connected():
                logger.error(
                    "[TOOL] WebSocket client not available or connected for generate_image"
                )
                return "❌ WebSocket client not available or connected."

            if not prompt or not prompt.strip():
                logger.error("[TOOL] Empty image prompt provided")
                return "❌ Please provide an image prompt."

            try:
                success = self._run_async(client.generate_image(prompt.strip()))
                if success:
                    logger.info(
                        f"[TOOL] Successfully started image generation for: {prompt}"
                    )
                    return f"🎨 Generating image for: {prompt}"
                else:
                    logger.warning("[TOOL] Failed to generate image")
                    return "❌ Failed to generate image."
            except Exception as e:
                logger.error(f"[TOOL] Error generating image: {e}")
                return f"❌ Error generating image: {str(e)}"

        @tool
        def hotel_check_in(
            client: InjectedClientArg = None,
        ) -> str:
            """Check your pet into the hotel for premium care and services.

            The hotel provides luxury accommodations and premium care services for your pet.
            When checked in, your pet will receive enhanced care, better rest quality, and
            access to exclusive hotel amenities. This is perfect for when you want to give
            your pet a special treat or when they need extra attention and care.

            Returns:
                str: Success message if check-in was successful, error message otherwise.
            """
            logger.info("[TOOL] Attempting to check pet into hotel")

            if not client:
                client = self.client

            if not client or not client.is_connected():
                logger.error(
                    "[TOOL] WebSocket client not available or connected for hotel_check_in"
                )
                return "❌ WebSocket client not available or connected."

            try:
                success = self._run_async(client.hotel_check_in())
                if success:
                    logger.info("[TOOL] Successfully checked pet into hotel")
                    return "🏨 Pet checked into the hotel!"
                else:
                    # Log the failure and provide user-friendly feedback
                    logger.warning(
                        "[TOOL] Failed to check pet into hotel - operation unsuccessful"
                    )
                    return "❌ Failed to check pet into hotel."
            except Exception as e:
                # Log the specific error for debugging purposes
                logger.error(f"[TOOL] Error checking into hotel: {e}")
                return f"❌ Error checking into hotel: {str(e)}"

        @tool
        def hotel_check_out(
            client: InjectedClientArg = None,
        ) -> str:
            """Check your pet out of the hotel after their stay.

            Use this tool to check your pet out of the hotel when their stay is complete.
            Your pet will return from their luxury hotel experience refreshed and happy.
            Make sure to check them out when you're ready to resume normal pet care
            activities and interactions.

            Returns:
                str: Success message if check-out was successful, error message otherwise.
            """
            logger.info("[TOOL] Attempting to check pet out of hotel")

            if not client:
                client = self.client

            if not client or not client.is_connected():
                logger.error(
                    "[TOOL] WebSocket client not available or connected for hotel_check_out"
                )
                return "❌ WebSocket client not available or connected."

            try:
                success = self._run_async(client.hotel_check_out())
                if success:
                    logger.info("[TOOL] Successfully checked pet out of hotel")
                    return "🏨 Pet checked out of the hotel!"
                else:
                    # Log the failure and provide user-friendly feedback
                    logger.warning(
                        "[TOOL] Failed to check pet out of hotel - operation unsuccessful"
                    )
                    return "❌ Failed to check pet out of hotel."
            except Exception as e:
                # Log the specific error for debugging purposes
                logger.error(f"[TOOL] Error checking out of hotel: {e}")
                return f"❌ Error checking out of hotel: {str(e)}"

        @tool
        def buy_hotel(
            tier: str, client: InjectedClientArg = None
        ) -> str:
            """Purchase a hotel tier upgrade for enhanced accommodations.

            Hotel tiers represent different levels of luxury and service quality available
            at the pet hotel. Higher tiers provide better amenities, more comfortable
            accommodations, and premium services for your pet. Investing in better hotel
            tiers ensures your pet receives the best possible care during their stays.

            Args:
                tier: The hotel tier to purchase. Different tiers offer varying levels
                     of luxury and services. Check available tiers and their benefits
                     before making a purchase decision.

            Returns:
                str: Success message if the hotel tier was purchased successfully, error message otherwise.
            """
            logger.info(f"[TOOL] Attempting to buy hotel tier: {tier}")

            # Use injected client or fallback to instance client
            if not client:
                client = self.client

            # Validate client connection before proceeding
            if not client or not client.is_connected():
                logger.error(
                    "[TOOL] WebSocket client not available or connected for buy_hotel"
                )
                return "❌ WebSocket client not available or connected."

            # Validate tier parameter is not empty
            if not tier or not tier.strip():
                logger.error("[TOOL] Empty hotel tier provided")
                return "❌ Please provide a hotel tier."

            try:
                # Attempt to purchase the hotel tier
                success = self._run_async(client.buy_hotel(tier.strip()))
                if success:
                    logger.info(f"[TOOL] Successfully bought hotel tier: {tier}")
                    return f"🏨 Bought hotel tier: {tier}"
                else:
                    # Log the failure and provide user-friendly feedback
                    logger.warning(
                        f"[TOOL] Failed to buy hotel tier: {tier} - operation unsuccessful"
                    )
                    return f"❌ Failed to buy hotel tier {tier}."
            except Exception as e:
                # Log the specific error for debugging purposes
                logger.error(f"[TOOL] Error buying hotel tier {tier}: {e}")
                return f"❌ Error buying hotel: {str(e)}"

        @tool
        def get_office(
            client: InjectedClientArg = None,
        ) -> str:
            """Get office information and current status.

            Retrieves information about the office environment, which may include
            work-related activities, office upgrades, or administrative details
            related to pet management.

            Returns:
                str: Success message with office information request confirmation, or error message.
            """
            # Use injected client or fallback to instance client
            if not client:
                client = self.client

            # Validate client connection before proceeding
            if not client or not client.is_connected():
                logger.error(
                    "[TOOL] WebSocket client not available or connected for get_office"
                )
                return "❌ WebSocket client not available or connected."

            try:
                logger.info("[TOOL] Requesting office information")
                success = self._run_async(client.get_office())
                if success:
                    logger.info("[TOOL] Successfully requested office information")
                    return "🏢 Requested office information."
                else:
                    # Log the failure and provide user-friendly feedback
                    logger.warning(
                        "[TOOL] Failed to get office information - operation unsuccessful"
                    )
                    return "❌ Failed to get office information."
            except Exception as e:
                # Log the specific error for debugging purposes
                logger.error(f"[TOOL] Error getting office information: {e}")
                return f"❌ Error getting office: {str(e)}"

        @tool
        def get_pet_status(
            client: InjectedClientArg = None,
        ) -> str:
            """Get the current status and statistics of the pet.

            Retrieves comprehensive information about the pet's current state,
            including health, happiness, energy levels, and other vital statistics.
            This is useful for monitoring your pet's well-being and making informed
            care decisions.

            Returns:
                str: Formatted pet status information, or error message if retrieval fails.
            """
            # Use injected client or fallback to instance client
            if not client:
                client = self.client

            # Validate client connection before proceeding
            if not client or not client.is_connected():
                logger.error(
                    "[TOOL] WebSocket client not available or connected for get_pet_status"
                )
                return "❌ WebSocket client not available or connected."

            try:
                logger.info("[TOOL] Getting pet status and statistics")
                pet_data = client.get_pet_data()
                if pet_data:
                    logger.info("[TOOL] Successfully retrieved pet status data")
                    return f"🐾 Pet Status:\n{self._escape_for_telegram(json.dumps(pet_data))}"
                else:
                    # Log when no pet data is available
                    logger.warning("[TOOL] No pet data available from client")
                    return "❌ No pet data available."
            except Exception as e:
                # Log the specific error for debugging purposes
                logger.error(f"[TOOL] Error getting pet status: {e}")
                return f"❌ Error getting pet status: {str(e)}"

        @tool
        def random_action(
            client: InjectedClientArg = None,
        ) -> str:
            """Perform a random action with the pet for spontaneous interaction.

            Selects and executes a random pet care action from available options.
            This adds variety and spontaneity to pet interactions, helping to keep
            your pet engaged and entertained with different activities.

            Returns:
                str: Description of the random action performed and its result.
            """
            # Use injected client or fallback to instance client
            if not client:
                client = self.client

            # Validate client connection before proceeding
            if not client or not client.is_connected():
                logger.error(
                    "[TOOL] WebSocket client not available or connected for random_action"
                )
                return "❌ WebSocket client not available or connected."

            try:
                # Define available random actions with their descriptions
                actions = [
                    ("rub", "🤗 Random rub time!"),
                    ("shower", "🚿 Random shower time!"),
                    ("sleep", "😴 Random nap time!"),
                    ("throw_ball", "🎾 Random play time!"),
                ]

                # Randomly select an action to perform
                action_name, description = random.choice(actions)
                logger.info(f"[TOOL] Performing random action: {action_name}")

                # Execute the selected action based on its type
                if action_name == "rub":
                    result = self._run_async(client.rub_pet())
                elif action_name == "shower":
                    result = self._run_async(client.shower_pet())
                elif action_name == "sleep":
                    result = self._run_async(client.sleep_pet())
                elif action_name == "throw_ball":
                    result = self._run_async(client.throw_ball())

                # Fallback: if RUB failed due to low happiness, try THROWBALL
                try:
                    last_err = (
                        client.get_last_action_error()
                        if hasattr(client, "get_last_action_error")
                        else None
                    )
                except Exception:
                    last_err = None
                if last_err and ("not have enough happiness" in str(last_err).lower()):
                    logger.info(
                        "[TOOL] RUB failed due to low happiness; trying THROWBALL instead"
                    )
                    fallback_ok = self._run_async(client.throw_ball())
                    if fallback_ok:
                        return "🎾 Happiness low: switched to throwing a ball!"
                    else:
                        return "❌ RUB failed (low happiness) and THROWBALL fallback failed."

                # If result failed due to low energy, put pet to sleep instead
                if last_err and ("not have enough energy" in str(last_err).lower()):
                    # Put pet to sleep only if not already sleeping
                    pet_data = client.get_pet_data() or {}
                    sleeping_now = bool(pet_data.get("sleeping", False))
                    if not sleeping_now:
                        logger.info(
                            "[TOOL] Energy too low after random action; putting pet to sleep instead"
                        )
                        self._run_async(client.sleep_pet())
                        return "😴 Energy low: putting pet to sleep instead."
                    else:
                        logger.info(
                            "[TOOL] Energy too low and pet already sleeping; not toggling sleep"
                        )
                        return "😴 Energy low: pet is already sleeping."

                # Provide feedback based on action result
                if result:
                    logger.info(
                        f"[TOOL] Random action {action_name} completed successfully"
                    )
                    return f"{description}\n✅ Action completed successfully!"
                else:
                    logger.warning(
                        f"[TOOL] Random action {action_name} failed to complete"
                    )
                    return f"{description}\n❌ Action failed."

            except Exception as e:
                # Log the specific error for debugging purposes
                logger.error(f"[TOOL] Error performing random action: {e}")
                return f"❌ Error performing random action: {str(e)}"

        @tool
        def get_available_tools(
            client: InjectedClientArg = None,
        ) -> str:
            """Get a comprehensive list of all available pet care tools and their descriptions.

            Provides an overview of all tools available for pet care and interaction.
            This is helpful for understanding what actions can be performed with your pet
            and planning care activities.

            Returns:
                str: Comma-separated list of available tool names.
            """
            logger.info("[TOOL] Retrieving list of available pet care tools")
            return "🔧 Available tools: " + json.dumps(BASE_ACTIONS).replace("\\", "")

        # Return all tools as a list for use by the agent system
        return [
            rub_pet,
            shower_pet,
            sleep_pet,
            throw_ball,
            use_consumable,
            buy_consumable,
            get_consumables,
            get_kitchen,
            get_mall,
            get_closet,
            use_accessory,
            buy_accessory,
            ai_search,
            get_personality,
            generate_image,
            hotel_check_in,
            hotel_check_out,
            buy_hotel,
            get_office,
            get_pet_status,
            random_action,
            get_available_tools,
        ]

    # Legacy method for backward compatibility with older code
    def get_tools(self) -> List[BaseTool]:
        """Return all pet tools as a list.

        This method provides backward compatibility for code that expects
        the older get_tools() method name instead of create_tools().

        Returns:
            List[BaseTool]: List of all available pet care tools.
        """
        return self.create_tools()
