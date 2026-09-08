"""Package entry point: run with `uv run python -m bot.main`."""

import asyncio

from main import main

if __name__ == "__main__":
    asyncio.run(main())
