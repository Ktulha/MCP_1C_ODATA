import httpx
import logging
from datetime import datetime
from mcp_1c_odata.config import config

logger = logging.getLogger("mcp-1c-odata.odata")


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
        except httpx.TimeoutException as e:
            logger.error(
                f"⏱️ Таймаут ({self.timeout}с) при запросе {endpoint}: {type(e).__name__}")
            return {"error": f"Таймаут соединения с {endpoint} ({self.timeout}с)"}
        except httpx.HTTPStatusError as e:
            logger.error(
                f"❌ HTTP {e.response.status_code} при запросе {endpoint}: {e.response.text[:200]}")
            return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except httpx.RequestError as e:
            logger.error(
                f"🔌 Ошибка соединения с {endpoint}: {type(e).__name__} - {str(e)}")
            return {"error": f"Ошибка соединения с {endpoint}: {type(e).__name__} - {str(e)}"}
        except Exception as e:
            logger.error(
                f"💥 Неожиданная ошибка при запросе {endpoint}: {type(e).__name__} - {str(e)}")
            return {"error": f"Неожиданная ошибка при запросе {endpoint}: {type(e).__name__} - {str(e)}"}

    async def get_all_pages(self, endpoint: str, params: dict | None = None, page_size: int = 5000, entity_alias: str = "") -> dict:
        """Загрузка всех записей с пагинацией. Возвращает dict с записями и статистикой."""
        all_records: list[dict] = []
        skip = 0
        batch_num = 0
        if params is None:
            params = {}
        params["$format"] = "json"
        params["$top"] = page_size

        logger.info(
            f"📥 Начинаю загрузку {entity_alias or endpoint} (batch_size={page_size})")

        while True:
            batch_num += 1
            params["$skip"] = skip

            response = await self.get(endpoint, params=params)
            if "error" in response:
                logger.error(
                    f"❌ Ошибка на батче #{batch_num} ({endpoint}, skip={skip}): {response['error']}")
                raise Exception(
                    f"Ошибка OData на батче #{batch_num}: {response['error']}")

            records = response.get("value", [])
            all_records.extend(records)

            logger.info(
                f"  📦 Батч #{batch_num}: получено {len(records)} записей (skip={skip}, всего={len(all_records)})")

            if not records or len(records) < page_size:
                logger.info(
                    f"✅ Загрузка {entity_alias or endpoint} завершена: {len(all_records)} записей в {batch_num} батчах")
                break

            skip += page_size

        return {
            "records": all_records,
            "batches": batch_num,
            "total_fetched": len(all_records)
        }

    async def get_register_balance(self, endpoint: str, condition: str = "", period: datetime | None = None, page_size: int = 5000, entity_alias: str = "") -> dict:
        """
        Получение остатков (Balance) из Регистра накопления 1С.
        Параметры Condition и Dimensions опциональны. Если не нужны, их лучше опустить.
        """
        period = period or datetime.now()
        period_str = period.strftime("%Y-%m-%dT%H:%M:%S")

        # Формируем параметры только если условие не пустое
        if condition and condition.strip():
            safe_condition = condition.replace("'", "''")
            func_path = f"{endpoint}/Balance(Condition='{safe_condition}',Dimensions='',Period=datetime'{period_str}')"
        else:
            # Самый безопасный и правильный способ получить все остатки на дату в 1С
            func_path = f"{endpoint}/Balance(Period=datetime'{period_str}')"

        all_records: list[dict] = []
        skip = 0
        batch_num = 0
        params = {"$format": "json", "$top": page_size}

        logger.info(
            f"📥 Начинаю Balance для {entity_alias or endpoint} на {period_str}")

        try:
            while True:
                batch_num += 1
                params["$skip"] = skip
                response = await self.get(func_path, params=params)

                if "error" in response:
                    if "Метод не найден" in response.get("error", "") or "Not found" in response.get("error", ""):
                        logger.warning(
                            f"⚠️ Функция Balance не найдена для '{entity_alias}'. "
                            f"Проверьте настройки публикации OData (должны быть включены виртуальные таблицы)."
                        )
                    raise Exception(
                        f"Ошибка OData Balance на батче #{batch_num}: {response['error']}")

                records = response.get("value", [])
                all_records.extend(records)

                logger.info(
                    f"  📦 Balance батч #{batch_num}: получено {len(records)} записей "
                    f"(skip={skip}, всего={len(all_records)})"
                )

                if not records or len(records) < page_size:
                    break

                skip += page_size

            logger.info(
                f"✅ Balance {entity_alias or endpoint} завершён: {len(all_records)} записей в {batch_num} батчах")
            return {
                "records": all_records,
                "batches": batch_num,
                "total_fetched": len(all_records)
            }
        except Exception as e:
            logger.error(
                f"💥 Критическая ошибка при выполнении Balance: {e}", exc_info=True)
            raise

    async def get_slice_last_all(self, endpoint: str, condition: str = "", period: datetime | None = None, page_size: int = 5000, entity_alias: str = "") -> dict:
        """
        Получение моментального среза (SliceLast) из Регистра сведений 1С.
        """
        period = period or datetime.now()
        period_str = period.strftime("%Y-%m-%dT%H:%M:%S")

        # Аналогично, Condition опционален
        if condition and condition.strip():
            safe_condition = condition.replace("'", "''")
            func_path = f"{endpoint}/SliceLast(Condition='{safe_condition}',Period=datetime'{period_str}')"
        else:
            func_path = f"{endpoint}/SliceLast(Period=datetime'{period_str}')"

        all_records: list[dict] = []
        skip = 0
        batch_num = 0
        params = {"$format": "json", "$top": page_size}

        logger.info(
            f"📥 Начинаю SliceLast для {entity_alias or endpoint} на {period_str}")

        try:
            while True:
                batch_num += 1
                params["$skip"] = skip
                response = await self.get(func_path, params=params)

                if "error" in response:
                    if "Метод не найден" in response.get("error", "") or "Not found" in response.get("error", ""):
                        logger.warning(
                            f"⚠️ Функция SliceLast не найдена для '{entity_alias}'. "
                            f"Проверьте настройки публикации OData."
                        )
                    raise Exception(
                        f"Ошибка OData SliceLast на батче #{batch_num}: {response['error']}")

                records = response.get("value", [])
                all_records.extend(records)

                logger.info(
                    f"  📦 SliceLast батч #{batch_num}: получено {len(records)} записей "
                    f"(skip={skip}, всего={len(all_records)})"
                )

                if not records or len(records) < page_size:
                    break

                skip += page_size

            logger.info(
                f"✅ SliceLast {entity_alias or endpoint} завершён: {len(all_records)} записей в {batch_num} батчах")
            return {
                "records": all_records,
                "batches": batch_num,
                "total_fetched": len(all_records)
            }
        except Exception as e:
            logger.error(
                f"💥 Критическая ошибка при выполнении SliceLast: {e}", exc_info=True)
            raise

    async def get_slice_first_all(self, endpoint: str, condition: str = "", period: datetime | None = None, page_size: int = 5000, entity_alias: str = "") -> dict:
        """
        Получение среза первых (SliceFirst) из Регистра сведений 1С.
        """
        period = period or datetime.now()
        period_str = period.strftime("%Y-%m-%dT%H:%M:%S")

        if condition and condition.strip():
            safe_condition = condition.replace("'", "''")
            func_path = f"{endpoint}/SliceFirst(Condition='{safe_condition}',Period=datetime'{period_str}')"
        else:
            func_path = f"{endpoint}/SliceFirst(Period=datetime'{period_str}')"

        all_records: list[dict] = []
        skip = 0
        batch_num = 0
        params = {"$format": "json", "$top": page_size}

        logger.info(
            f"📥 Начинаю SliceFirst для {entity_alias or endpoint} на {period_str}")

        try:
            while True:
                batch_num += 1
                params["$skip"] = skip
                response = await self.get(func_path, params=params)

                if "error" in response:
                    if "Метод не найден" in response.get("error", "") or "Not found" in response.get("error", ""):
                        logger.warning(
                            f"⚠️ Функция SliceFirst не найдена для '{entity_alias}'.")
                    raise Exception(
                        f"Ошибка OData SliceFirst на батче #{batch_num}: {response['error']}")

                records = response.get("value", [])
                all_records.extend(records)

                logger.info(
                    f"  📦 SliceFirst батч #{batch_num}: получено {len(records)} записей (skip={skip}, всего={len(all_records)})")

                if not records or len(records) < page_size:
                    break
                skip += page_size

            logger.info(
                f"✅ SliceFirst {entity_alias or endpoint} завершён: {len(all_records)} записей в {batch_num} батчах")
            return {"records": all_records, "batches": batch_num, "total_fetched": len(all_records)}
        except Exception as e:
            logger.error(
                f"💥 Критическая ошибка при выполнении SliceFirst: {e}", exc_info=True)
            raise


odata_client = ODataClient()
