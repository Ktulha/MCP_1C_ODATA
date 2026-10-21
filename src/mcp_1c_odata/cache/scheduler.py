import logging
import pandas as pd
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime

from mcp_1c_odata.config import config
from mcp_1c_odata.cache.storage import cache_storage
from mcp_1c_odata.cache.bom_builder import bom_builder
from mcp_1c_odata.cache.forecast_builder import forecast_builder
from mcp_1c_odata.odata.client import odata_client

logger = logging.getLogger("mcp-1c-odata.scheduler")


class CacheScheduler:
    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self._setup_jobs()

    def _setup_jobs(self):
        """Настройка cron-задач из config.yaml.

        Если значение cron пустое или None — задание не добавляется.
        """
        scheduler_config = config.scheduler

        if not scheduler_config.get("enabled", False):
            logger.info("Планировщик отключён в конфигурации")
            return

        # Инкрементальное обновление
        incremental_cron = scheduler_config.get("incremental_cron")
        if incremental_cron:
            self.scheduler.add_job(
                self.incremental_update_all,
                CronTrigger.from_crontab(incremental_cron),
                id="incremental_update",
                name="Incremental cache update",
                replace_existing=True
            )
            logger.info(f"Инкрементальное обновление: {incremental_cron}")
        else:
            logger.info(
                "Инкрементальное обновление: отключено (cron не задан)")

        # Полное обновление
        full_cron = scheduler_config.get("full_cron")
        if full_cron:
            self.scheduler.add_job(
                self.full_update_all,
                CronTrigger.from_crontab(full_cron),
                id="full_update",
                name="Full cache resync",
                replace_existing=True
            )
            logger.info(f"Полное обновление: {full_cron}")
        else:
            logger.info("Полное обновление: отключено (cron не задан)")

    # ============================================================
    # МАССОВОЕ ОБНОВЛЕНИЕ
    # ============================================================

    async def incremental_update_all(self):
        """Инкрементальное обновление всех сущностей из allowlist"""
        logger.info("Запуск инкрементального обновления кэша")
        updated_count = 0
        skipped_count = 0

        for item in config.allowlist:
            alias = item.get("alias")
            name = item.get("name")
            is_view = item.get("is_view", False)
            cache_mode = item.get("cache_mode", "full")

            if is_view:
                continue

            # Пропускаем производные витрины — они пересчитываются в rebuild_derived_data()
            if cache_mode == "derived":
                skipped_count += 1
                continue

            if cache_mode == "filtered_only":
                logger.info(
                    f"Пропуск {alias}: полный кэш запрещён (cache_mode=filtered_only)")
                skipped_count += 1
                continue

            if cache_mode == "slice_last":
                logger.info(
                    f"Пропуск {alias}: для slice_last используйте полное обновление")
                skipped_count += 1
                continue

            if alias and name:
                await self.incremental_update(alias, name)
                updated_count += 1

        # Пересчёт производных витрин (BOM + прогноз)
        self.rebuild_derived_data()

        # Пересоздание SQL Views
        self._update_views()

        logger.info(
            f"Инкрементальное обновление завершено: обновлено {updated_count}, пропущено {skipped_count}")

    async def full_update_all(self):
        """Полное обновление всех сущностей из allowlist"""
        logger.info("Запуск полного обновления кэша")
        updated_count = 0
        skipped_count = 0

        for item in config.allowlist:
            alias = item.get("alias")
            name = item.get("name")
            is_view = item.get("is_view", False)
            cache_mode = item.get("cache_mode", "full")

            if is_view:
                continue

            # Пропускаем производные витрины — они пересчитываются в rebuild_derived_data()
            if cache_mode == "derived":
                skipped_count += 1
                continue

            if cache_mode == "filtered_only":
                logger.info(
                    f"Пропуск {alias}: полный кэш запрещён (cache_mode=filtered_only)")
                skipped_count += 1
                continue

            if alias and name:
                await self.full_update(alias, name, cache_mode)
                updated_count += 1

        # Пересчёт производных витрин (BOM + прогноз)
        self.rebuild_derived_data()

        # Пересоздание SQL Views
        self._update_views()

        logger.info(
            f"Полное обновление завершено: обновлено {updated_count}, пропущено {skipped_count}")

    # ============================================================
    # ПЕРЕСЧЁТ ПРОИЗВОДНЫХ ВИТРИН
    # ============================================================

    def rebuild_derived_data(self):
        """Пересчёт всех производных витрин: BOM, прогноз, потребность.

        Вызывается после обновления базовых сущностей.
        Порядок важен: сначала BOM, потом витрины прогноза (они зависят от BOM).
        """
        logger.info("=== Пересчёт производных витрин ===")

        # 1. BOM (Bill of Materials) — дерево и плоская таблица
        bom_results = bom_builder.build_all()
        bom_ok = sum(1 for v in bom_results.values() if v)
        logger.info(f"✓ BOM построен: {bom_ok}/{len(bom_results)} витрин")

        # 2. Витрины прогноза (зависят от BOM)
        forecast_results = forecast_builder.build_all()
        forecast_ok = sum(1 for v in forecast_results.values() if v)
        logger.info(
            f"✓ Прогноз построен: {forecast_ok}/{len(forecast_results)} витрин")

        logger.info("=== Пересчёт производных витрин завершён ===")

    # ============================================================
    # ОБНОВЛЕНИЕ ОДНОЙ СУЩНОСТИ
    # ============================================================

    async def incremental_update(self, alias: str, full_name: str):
        """Инкрементальное обновление одной сущности (только для режима 'full')"""
        try:
            last_updated = cache_storage.get_last_updated(alias)

            params = {"$format": "json"}
            if last_updated:
                # Фильтрация по DataVersion (стандартное поле 1С)
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
                # Обновляем существующие записи по Ref_Key
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

    async def full_update(self, alias: str, full_name: str, cache_mode: str = "full"):
        """Полное обновление одной сущности с учётом режима кэширования."""
        try:
            if cache_mode == "slice_last":
                # Для остатков используем SliceLast на текущий момент
                logger.info(
                    f"{alias}: запрашиваем актуальный срез (SliceLast) на {datetime.now().strftime('%Y-%m-%d %H:%M')}")
                records = await odata_client.get_slice_last_all(full_name, page_size=5000)
            else:
                # Обычная полная загрузка с пагинацией
                records = await odata_client.get_all_pages(full_name, page_size=5000)

            if not records:
                logger.warning(f"{alias}: 1С вернула пустой результат")
                return

            df = pd.DataFrame(records)
            cache_storage.save_entity(alias, df)
            logger.info(
                f"{alias}: успешно обновлено {len(records)} записей (режим: {cache_mode})")

        except Exception as e:
            logger.error(
                f"Ошибка обновления {alias} (режим: {cache_mode}): {e}", exc_info=True)

    # ============================================================
    # ОБНОВЛЕНИЕ SQL VIEWS
    # ============================================================

    def _update_views(self):
        """Создание или обновление скомпилированных представлений (Views)"""
        views_config = [
            item for item in config.allowlist if item.get("is_view")]
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

    # ============================================================
    # УПРАВЛЕНИЕ ПЛАНИРОВЩИКОМ
    # ============================================================

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
