"""Compatibility entrypoint; implementation lives in studio_main.py."""
import asyncio

from studio_main import *  # noqa: F401,F403
from studio_main import main


if __name__ == "__main__":
    asyncio.run(main())
