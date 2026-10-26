"""Compatibility entrypoint; implementation lives in main.py for packaging."""
import asyncio

from main import *  # noqa: F401,F403
from main import main


if __name__ == "__main__":
    asyncio.run(main())
