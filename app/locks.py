import asyncio

# Global locks for the application
finance_lock = asyncio.Lock()
packages_lock = asyncio.Lock()
refresh_lock = asyncio.Lock()
estoque_lock = asyncio.Lock()
