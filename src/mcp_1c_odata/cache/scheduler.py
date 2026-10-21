import logging
import pandas as pd
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime

from mcp_1c_odata.config import config
from mcp_1c_odata.cache.storage import cache_storage
from mcp_1c_odata.odata.client import odata_client

logger = logging.getLogger("mcp-1c-odata.scheduler")


class CacheScheduler:
    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self._setup_jobs()

    def _setup_jobs(self):
        """Настройка cron-задач из config.yaml"""
        scheduler_config = config.scheduler

        if not scheduler_config.get("enabled", False):
            logger.info("Планировщик полностью отключён в конфигурации")
            return

        # 1. Инкрементальное обновление
        incremental_cron = scheduler_config.get("incremental_cron")
        if incremental_cron:  # Проверка на None и пустую строку ""
            self.scheduler.add_job(
                self.incremental_update_all,
                CronTrigger.from_crontab(incremental_cron),
                id="incremental_update",
                name="Incremental cache update",
                replace_existing=True
            )
            logger.info(
                f"✓ Инкрементальное обновление включено: {incremental_cron}")
        else:
            logger.info("✗ Инкрементальное обновление отключено")

        # 2. Полное обновление
        full_cron = scheduler_config.get("full_cron")
        if full_cron:  # Проверка на None и пустую строку ""
            self.scheduler.add_job(
                self.full_update_all,
                CronTrigger.from_crontab(full_cron),
                id="full_update",
                name="Full cache resync",
                replace_existing=True
            )
            logger.info(f"✓ Полное обновление включено: {full_cron}")
        else:
            logger.info("✗ Полное обновление отключено")

    async def incremental_update_all(self):
        """Инкрементальное обновление всех сущностей из allowlist"""
        logger.info("Запуск инкрементального обновления кэша")
        for item in config.allowlist:
            alias = item.get("alias")
            name = item.get("name")
            is_view = item.get("is_view", False)

            # Пропускаем views (двойная проверка: по флагу и по префиксу имени)
            if alias and name and not is_view and not name.startswith("View_"):
                await self.incremental_update(alias, name)

        # После обновления данных пересоздаем Views
        self._update_views()

    async def full_update_all(self):
        """Полное обновление всех сущностей из allowlist"""
        logger.info("Запуск полного обновления кэша")
        for item in config.allowlist:
            alias = item.get("alias")
            name = item.get("name")
            is_view = item.get("is_view", False)

            # Пропускаем views (двойная проверка: по флагу и по префиксу имени)
            if alias and name and not is_view and not name.startswith("View_"):
                await self.full_update(alias, name)

        # После обновления данных пересоздаем Views
        self._update_views()

    def _update_views(self):
        """Создание или обновление скомпилированных представлений (Views)"""
        views_config = [item for item in config.allowlist if item.get(
            "is_view") or (item.get("name") or "").startswith("View_")]
        if not views_config:
            logger.info("Скомпилированные представления (Views) не настроены")
            return

        logger.info(
            f"Создание/обновление {len(views_config)} скомпилированных представлений (Views)...")
        results = cache_storage.create_views(views_config)

        success_count = sum(1 for v in results.values() if v)
        failed_count = len(results) - success_count

        if failed_count > 0:
            logger.warning(
                f"⚠ Views обновлены: успешно {success_count}/{len(results)}, ошибок: {failed_count}")
            for alias, success in results.items():
                if not success:
                    logger.warning(f"  ✗ View '{alias}' не создан")
        else:
            logger.info(
                f"✓ Views обновлены: успешно {success_count}/{len(results)}")

    async def incremental_update(self, alias: str, full_name: str):
        """Инкрементальное обновление одной сущности"""
        try:
            last_updated = cache_storage.get_last_updated(alias)

            params = {"$format": "json"}
            if last_updated:
                params["$filter"] = f"DataVersion gt datetime'{last_updated.isoformat()}'"

            response = await odata_client.get(full_name, params=params)

            if "error" in response:
                logger.error(
                    f"Ошибка при инкрементальном обновлении {alias}: {response['error']}")
                return

            records = response.get("value", [])

            if not records:
                logger.info(f"{alias}: нет новых записей")
                return

            existing_df = cache_storage.load_entity(alias)
            new_df = pd.DataFrame(records)

            if existing_df is not None and len(existing_df) > 0:
                combined_df = pd.concat([existing_df, new_df]).drop_duplicates(
                    subset=['Ref_Key'], keep='last'
                )
                cache_storage.save_entity(alias, combined_df)
                logger.info(
                    f"{alias}: обновлено {len(records)} записей, всего {len(combined_df)}")
            else:
                cache_storage.save_entity(alias, new_df)
                logger.info(f"{alias}: создано {len(records)} записей")

        except Exception as e:
            logger.error(
                f"Ошибка инкрементального обновления {alias}: {e}", exc_info=True)

    async def full_update(self, alias: str, full_name: str):
        """Полное обновление одной сущности"""
        try:
            records = await odata_client.get_all_pages(full_name, page_size=5000)

            if not records:
                logger.warning(f"{alias}: 1С вернула пустой результат")
                return

            df = pd.DataFrame(records)
            cache_storage.save_entity(alias, df)
            logger.info(f"{alias}: полностью обновлено {len(records)} записей")

        except Exception as e:
            logger.error(
                f"Ошибка полного обновления {alias}: {e}", exc_info=True)

    def start(self):
        """Запустить планировщик"""
        if not config.scheduler.get("enabled", False):
            return
        self.scheduler.start()
        logger.info("Планировщик кэша запущен")

    def shutdown(self):
        """Остановить планировщик"""
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("Планировщик кэша остановлен")


cache_scheduler = CacheScheduler()
