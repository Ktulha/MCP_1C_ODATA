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
        """Настройка cron-задач из config.yaml."""
        scheduler_config = config.scheduler

        if not scheduler_config.get("enabled", False):
            logger.info(
                "Планировщик отключён в конфигурации (scheduler.enabled=false)")
            # Даже если планировщик выключен, мы не падаем, а просто выходим
            return

        def parse_cron(cron_expr: str | None, job_name: str) -> str | None:
            """Безопасный парсер cron: убирает лишнее 6-е поле (секунды), если оно есть."""
            if not cron_expr:
                return None
            parts = cron_expr.split()
            if len(parts) == 6:
                logger.warning(
                    f"⚠️ {job_name}: Cron '{cron_expr}' содержит 6 полей. APScheduler поддерживает только 5. Автоматически убираем первое поле (секунды).")
                return " ".join(parts[1:])
            return cron_expr

        # 1. Инкрементальное обновление
        incremental_cron = parse_cron(scheduler_config.get(
            "incremental_cron"), "incremental_cron")
        if incremental_cron:
            try:
                self.scheduler.add_job(
                    self.incremental_update_all,
                    CronTrigger.from_crontab(incremental_cron),
                    id="incremental_update",
                    name="Incremental cache update",
                    replace_existing=True
                )
                logger.info(
                    f"✓ Инкрементальное обновление настроено: {incremental_cron}")
            except ValueError as e:
                logger.error(
                    f"❌ Ошибка парсинга cron для incremental_cron: {e}. Убедитесь, что в config.yaml ровно 5 полей (например: '0 3 * * *')")

        # 2. Полное обновление
        full_cron = parse_cron(scheduler_config.get("full_cron"), "full_cron")
        if full_cron:
            try:
                self.scheduler.add_job(
                    self.full_update_all,
                    CronTrigger.from_crontab(full_cron),
                    id="full_update",
                    name="Full cache resync",
                    replace_existing=True
                )
                logger.info(f"✓ Полное обновление настроено: {full_cron}")
            except ValueError as e:
                logger.error(
                    f"❌ Ошибка парсинга cron для full_cron: {e}. Убедитесь, что в config.yaml ровно 5 полей (например: '0 3 * * *')")

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
        """Пересчёт всех производных витрин.

        ВАЖНО: вызывается через asyncio.to_thread(), чтобы не блокировать event loop.
        """
        import asyncio

        logger.info("=== Пересчёт производных витрин ===")

        # 1. BOM — в отдельном потоке
        logger.info("🔨 Построение BOM...")
        bom_results = asyncio.to_thread(bom_builder.build_all)
        # Но rebuild_derived_data вызывается из sync-контекста, поэтому используем executor
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            future_bom = executor.submit(bom_builder.build_all)
            bom_results = future_bom.result()  # ждём результат

        bom_ok = sum(1 for v in bom_results.values() if v)
        logger.info(f"✓ BOM построен: {bom_ok}/{len(bom_results)} витрин")

        # 2. Прогноз — в отдельном потоке
        logger.info("📊 Построение витрин прогноза...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            future_forecast = executor.submit(forecast_builder.build_all)
            forecast_results = future_forecast.result()

        forecast_ok = sum(1 for v in forecast_results.values() if v)
        logger.info(
            f"✓ Прогноз построен: {forecast_ok}/{len(forecast_results)} витрин")

        logger.info("=== Пересчёт производных витрин завершён ===")

    # ============================================================
    # ОБНОВЛЕНИЕ ОДНОЙ СУЩНОСТИ
    # ============================================================
    async def incremental_update(self, alias: str, full_name: str) -> dict:
        """Инкрементальное обновление одной сущности. Возвращает статистику."""
        start_time = datetime.now()
        logger.info(f"🔄 [{alias}] Начало инкрементального обновления")

        try:
            last_updated = cache_storage.get_last_updated(alias)
            params = {"$format": "json"}

            if last_updated:
                # В 1С OData фильтрация по DataVersion часто вызывает ошибку "Операция не разрешена".
                # Используем стандартное поле ModifiedDate (ДатаИзменения).
                # Форматируем без микросекунд для надежности парсера 1С.
                dt_str = last_updated.strftime("%Y-%m-%dT%H:%M:%S")
                params["$filter"] = f"ModifiedDate gt datetime'{dt_str}'"
                logger.info(f"  📅 Фильтр по ModifiedDate > {dt_str}")
            else:
                logger.info(f"  📅 Предыдущих обновлений нет, загружаю всё")

            response = await odata_client.get(full_name, params=params)
            if "error" in response:
                # Если даже ModifiedDate не сработал (редкость), прерываем с понятной ошибкой
                raise Exception(response["error"])

            records = response.get("value", [])
            if not records:
                logger.info(
                    f"✅ [{alias}] Нет новых записей ({(datetime.now() - start_time).total_seconds():.1f}с)")
                return {"batches": 1, "records": 0, "updated": 0, "status": "success"}

            existing_df = cache_storage.load_entity(alias)
            new_df = pd.DataFrame(records)

            if existing_df is not None and len(existing_df) > 0:
                # Обновляем существующие записи по Ref_Key, сохраняя последние изменения
                combined_df = pd.concat([existing_df, new_df]).drop_duplicates(
                    subset=['Ref_Key'], keep='last'
                )
                cache_storage.save_entity(alias, combined_df)
                logger.info(
                    f"✅ [{alias}] Обновлено {len(records)} записей, "
                    f"всего {len(combined_df)} ({(datetime.now() - start_time).total_seconds():.1f}с)"
                )
                return {"batches": 1, "records": len(records), "updated": len(combined_df), "status": "success"}
            else:
                cache_storage.save_entity(alias, new_df)
                logger.info(
                    f"✅ [{alias}] Создано {len(records)} записей "
                    f"({(datetime.now() - start_time).total_seconds():.1f}с)"
                )
                return {"batches": 1, "records": len(records), "updated": len(records), "status": "success"}

        except Exception as e:
            logger.error(
                f"💥 [{alias}] Ошибка инкрементального обновления: {e}", exc_info=True)
            raise

    async def full_update(self, alias: str, full_name: str, cache_mode: str = "full") -> dict:
        """Полное обновление одной сущности с учётом режима кэширования."""
        start_time = datetime.now()
        logger.info(
            f"🔄 [{alias}] Начало полного обновления (режим: {cache_mode})")

        try:
            if cache_mode == "slice_last":
                if full_name.startswith("AccumulationRegister_"):
                    logger.info(
                        f"  🎯 Запрашиваю остатки (Balance) на {datetime.now().strftime('%Y-%m-%d %H:%M')}")
                    result = await odata_client.get_register_balance(full_name, page_size=5000, entity_alias=alias)
                else:
                    logger.info(
                        f"  🎯 Запрашиваю срез последних (SliceLast) на {datetime.now().strftime('%Y-%m-%d %H:%M')}")
                    result = await odata_client.get_slice_last_all(full_name, page_size=5000, entity_alias=alias)

            elif cache_mode == "slice_first":
                logger.info(
                    f"  🎯 Запрашиваю срез первых (SliceFirst) на {datetime.now().strftime('%Y-%m-%d %H:%M')}")
                result = await odata_client.get_slice_first_all(full_name, page_size=5000, entity_alias=alias)

            else:
                result = await odata_client.get_all_pages(full_name, page_size=5000, entity_alias=alias)

            records = result.get("records", [])
            batches = result.get("batches", 1)

            if not records:
                logger.warning(
                    f"⚠️ [{alias}] 1С вернула пустой результат ({(datetime.now() - start_time).total_seconds():.1f}с)")
                return {"batches": batches, "records": 0, "status": "success"}

            df = pd.DataFrame(records)
            cache_storage.save_entity(alias, df)
            logger.info(
                f"✅ [{alias}] Успешно обновлено {len(records)} записей в {batches} батчах ({(datetime.now() - start_time).total_seconds():.1f}с)")
            return {"batches": batches, "records": len(records), "status": "success"}

        except Exception as e:
            logger.error(f"💥 [{alias}] Ошибка обновления: {e}", exc_info=True)
            raise
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
    # ДИНАМИЧЕСКОЕ УПРАВЛЕНИЕ ЗАДАНИЯМИ
    # ============================================================

    def load_jobs_from_db(self):
        """Загрузка заданий из БД при старте сервера"""
        from mcp_1c_odata.cache.schedule_storage import list_jobs

        jobs = list_jobs()
        loaded = 0
        for job in jobs:
            if not job["enabled"]:
                continue
            try:
                self._add_dynamic_job(job)
                loaded += 1
            except Exception as e:
                logger.error(f"Ошибка загрузки задания {job['name']}: {e}")

        if loaded > 0:
            logger.info(f"✓ Загружено {loaded} заданий из расписания")

    def _add_dynamic_job(self, job: dict):
        """Добавить задание в APScheduler"""
        from apscheduler.triggers.cron import CronTrigger

        job_id = f"dynamic_{job['id']}"
        trigger = CronTrigger.from_crontab(job["cron_expr"])

        job_type = job["job_type"]
        entities = job.get("entities")

        async def job_wrapper():
            from mcp_1c_odata.cache.schedule_storage import mark_run
            try:
                if job_type == "full":
                    if entities:
                        import json
                        alias_list = json.loads(entities)
                        for alias in alias_list:
                            entity_config = next(
                                (e for e in config.allowlist if e.get(
                                    "alias") == alias),
                                None
                            )
                            if not entity_config:
                                continue
                            full_name = entity_config.get("name")
                            if not full_name:  # <-- ЯВНАЯ ПРОВЕРКА ДЛЯ ТИПИЗАТОРА
                                continue
                            await self.full_update(
                                alias,
                                full_name,
                                entity_config.get("cache_mode", "full")
                            )
                    else:
                        await self.full_update_all()
                else:  # incremental
                    if entities:
                        import json
                        alias_list = json.loads(entities)
                        for alias in alias_list:
                            entity_config = next(
                                (e for e in config.allowlist if e.get(
                                    "alias") == alias),
                                None
                            )
                            if not entity_config:
                                continue
                            full_name = entity_config.get("name")
                            if not full_name:  # <-- ЯВНАЯ ПРОВЕРКА ДЛЯ ТИПИЗАТОРА
                                continue
                            await self.incremental_update(
                                alias,
                                full_name
                            )
                    else:
                        await self.incremental_update_all()

                mark_run(job["id"], "success")
            except Exception as e:
                logger.error(
                    f"Ошибка выполнения задания {job['name']}: {e}", exc_info=True)
                mark_run(job["id"], f"error: {str(e)[:100]}")

        self.scheduler.add_job(
            job_wrapper,
            trigger,
            id=job_id,
            name=job["name"],
            replace_existing=True
        )
        logger.info(f"✓ Задание '{job['name']}' добавлено: {job['cron_expr']}")

    def resync_jobs(self):
        """Полная пересинхронизация APScheduler с БД (после изменений)"""
        from mcp_1c_odata.cache.schedule_storage import list_jobs

        # Удаляем все динамические задания
        for job in self.scheduler.get_jobs():
            if job.id.startswith("dynamic_"):
                job.remove()

        # Загружаем заново
        self.load_jobs_from_db()

    def get_jobs_info(self) -> list[dict]:
        """Получить информацию о всех заданиях (включая статус в APScheduler)"""
        from mcp_1c_odata.cache.schedule_storage import list_jobs

        db_jobs = list_jobs()
        apscheduler_jobs = {
            j.id: j for j in self.scheduler.get_jobs() if j.id.startswith("dynamic_")
        }

        result = []
        for job in db_jobs:
            job_id = f"dynamic_{job['id']}"
            ap_job = apscheduler_jobs.get(job_id)

            # Безопасное получение next_run_time через getattr
            next_run_time = getattr(
                ap_job, "next_run_time", None) if ap_job else None

            result.append({
                **job,
                "next_run": next_run_time.isoformat() if next_run_time else None,
                "active_in_scheduler": ap_job is not None
            })

        return result

    # ============================================================
    # УПРАВЛЕНИЕ ПЛАНИРОВЩИКОМ
    # ============================================================

    def start(self):
        """Запустить планировщик"""
        if not config.scheduler.get("enabled", False):
            # Даже если планировщик отключён в конфиге, загружаем задания из БД
            # (они могут быть включены индивидуально)
            pass

        if not self.scheduler.running:
            self.scheduler.start()

        self.load_jobs_from_db()
        logger.info("Планировщик кэша запущен")

    def shutdown(self):
        """Остановить планировщик"""
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("Планировщик кэша остановлен")


cache_scheduler = CacheScheduler()
