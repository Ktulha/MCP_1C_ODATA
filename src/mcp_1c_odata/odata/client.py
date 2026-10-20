import httpx
from mcp_1c_odata.config import config


class ODataClient:
    def __init__(self):
        self.base_url = config.odata_base_url.rstrip("/")
        self.auth = (config.odata_user,
                     config.odata_password) if config.odata_user else None
        self.timeout = config.odata_timeout_sec

    async def get(self, endpoint: str, params: dict | None = None, headers: dict | None = None) -> dict:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"

        default_headers = {"Accept": "application/json"}
        if headers:
            default_headers.update(headers)

        try:
            async with httpx.AsyncClient(auth=self.auth, timeout=self.timeout) as client:
                response = await client.get(url, params=params, headers=default_headers)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except httpx.RequestError as e:
            return {"error": f"Ошибка соединения с {url}: {type(e).__name__} - {str(e)}"}
        except Exception as e:
            return {"error": f"Неожиданная ошибка при запросе {url}: {type(e).__name__} - {str(e)}"}

    async def get_all_pages(self, endpoint: str, params: dict | None = None, page_size: int = 5000) -> list[dict]:
        """Загрузка всех записей с пагинацией через $skip"""
        all_records = []
        skip = 0

        if params is None:
            params = {}

        params["$format"] = "json"
        params["$top"] = page_size

        while True:
            params["$skip"] = skip
            response = await self.get(endpoint, params=params)

            if "error" in response:
                raise Exception(f"Ошибка OData: {response['error']}")

            records = response.get("value", [])
            if not records:
                break

            all_records.extend(records)

            # Если вернулось меньше, чем page_size, значит, это последняя страница
            if len(records) < page_size:
                break

            skip += page_size

        return all_records


odata_client = ODataClient()
