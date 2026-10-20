import asyncio
from .server import main as async_main


def main():
    """Точка входа для CLI (синхронная обертка)"""
    asyncio.run(async_main())
