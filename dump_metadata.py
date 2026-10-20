import asyncio
import httpx
from mcp_1c_odata.config import config


async def main():
    url = f"{config.odata_base_url.rstrip('/')}/$metadata"
    auth = (config.odata_user, config.odata_password) if config.odata_user else None

    try:
        async with httpx.AsyncClient(auth=auth, timeout=config.odata_timeout_sec) as client:
            response = await client.get(url)
            response.raise_for_status()

            with open("metadata_dump.xml", "w", encoding="utf-8") as f:
                f.write(response.text)
            print("✓ Метаданные (XML) сохранены в metadata_dump.xml")

            print("\nНачало файла (первые 600 символов):")
            print(response.text[:600])
    except Exception as e:
        print(f"Ошибка: {e}")

if __name__ == "__main__":
    asyncio.run(main())
