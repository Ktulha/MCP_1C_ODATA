import io
import csv
import asyncio
import logging
from pathlib import Path
from contextlib import asynccontextmanager

import click
import uvicorn
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates

from mcp_1c_odata.admin import config as cfg
from mcp_1c_odata.admin.audit import init_audit, recent
from mcp_1c_odata.admin.auth import (
    _check_master_password, create_session_token,
    require_login, login_redirect, logout_redirect, audit,
)
from mcp_1c_odata.auth.db import init_db, list_tokens, create_token, revoke_token
from mcp_1c_odata.auth.roles import role_registry

# === ДОБАВЛЕННЫЕ ИМПОРТЫ ===
from mcp_1c_odata.cache.scheduler import cache_scheduler
from mcp_1c_odata.cache.storage import cache_storage
from mcp_1c_odata.cache.bom_builder import bom_builder          # <-- ДОБАВЛЕНО
from mcp_1c_odata.cache.forecast_builder import forecast_builder  # <-- ДОБАВЛЕНО
# ===========================

from mcp_1c_odata.config import config
from mcp_1c_odata.logs.storage import init_logs, recent_logs, stats as logs_stats, cleanup as logs_cleanup
from mcp_1c_odata.rate_limit.storage import get_counters, reset as reset_counter, cleanup as cleanup_counters
from mcp_1c_odata.rate_limit.limiter import rate_limiter

logger = logging.getLogger("mcp-1c-odata.admin")

TEMPLATES_DIR = Path(__file__).parent / "templates"
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

LOGIN_HTML = """
<!doctype html><html><head><meta charset="utf-8"><title>Login</title>
<style>
body{font-family:system-ui;max-width:360px;margin:80px auto;padding:0 16px}
input,select{width:100%;padding:8px;margin:6px 0;box-sizing:border-box;border:1px solid #ccc;border-radius:4px}
button{width:100%;padding:10px;background:#2563eb;color:#fff;border:0;cursor:pointer;border-radius:4px;margin-top:8px}
button:hover{background:#1d4ed8}
.err{color:#b91c1c;margin-top:8px;font-size:0.9em}
</style></head><body>
<h2>MCP Admin</h2>
<form method="post" action="/login">
  <label>Пароль</label>
  <input type="password" name="password" autofocus required autocomplete="current-password">
  <button type="submit">Войти</button>
</form>
{% if error %}<div class="err">{{ error }}</div>{% endif %}
</body></html>
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    init_audit()
    init_logs()
    from mcp_1c_odata.cache.schedule_storage import init_schedule_db
    init_schedule_db()
    login_tpl = TEMPLATES_DIR / "login.html"
    if not login_tpl.exists():
        login_tpl.write_text(LOGIN_HTML, encoding="utf-8")
    yield


app = FastAPI(title="MCP 1C OData — Admin", lifespan=lifespan)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# ============================================================
# АВТОРИЗАЦИЯ
# ============================================================

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str | None = None):
    return templates.TemplateResponse(request, "login.html", {"error": error})


@app.post("/login")
async def login_submit(request: Request, password: str = Form(...)):
    if not _check_master_password(password):
        audit("login_failed", request, details="invalid password")
        return templates.TemplateResponse(request, "login.html", {"error": "Неверный пароль"}, status_code=401)
    audit("login_ok", request)
    return login_redirect(create_session_token(), next_url="/")


@app.get("/logout")
async def logout(request: Request):
    audit("logout", request)
    return logout_redirect()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    require_login(request)
    return templates.TemplateResponse(request, "index.html", {})


# ============================================================
# ТОКЕНЫ
# ============================================================

@app.get("/tokens", response_class=HTMLResponse)
async def tokens_page(request: Request):
    require_login(request)
    tokens = list_tokens()
    roles = list(role_registry.roles.keys())

    role_entities = {}
    for role_name in roles:
        # ИСПРАВЛЕНО: передаем строку role_name
        allowed = role_registry.effective_entities(role_name)
        role_entities[role_name] = sorted(allowed)

    return templates.TemplateResponse(request, "tokens.html", {
        "tokens": tokens, "roles": roles, "role_entities": role_entities
    })


@app.get("/api/roles/{role_name}/entities", response_class=JSONResponse)
async def get_role_entities(request: Request, role_name: str):
    require_login(request)
    try:
        # ИСПРАВЛЕНО: передаем строку role_name, а не результат role_registry.get() (который является dict)
        allowed = sorted(role_registry.effective_entities(role_name))
        return {"entities": allowed}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/tokens/create")
async def create_token_route(
    request: Request,
    name: str = Form(...),
    role: str = Form(...),
    scope: str = Form(""),
    days: int = Form(30)
):
    require_login(request)
    scope_list = [s.strip() for s in scope.split(
        ",") if s.strip()] if scope else None
    try:
        final_scope = role_registry.validate_scope(role, scope_list)
        raw_token = create_token(name, ",".join(
            final_scope), role=role, days=days)
        audit("token_created", request, target=name,
              details=f"role={role}, scope={','.join(final_scope)}")
        return HTMLResponse(f"""
            <div class="success-box" style="background:#dcfce7;color:#166534;padding:12px;border-radius:4px;margin-bottom:16px">
                <strong>✓ Токен создан!</strong><br>
                <code style="background:#fff;padding:4px;display:block;margin:8px 0;word-break:break-all">{raw_token}</code>
                <small>⚠ Сохраните его сейчас, он больше не будет показан.</small>
            </div>
        """)
    except ValueError as e:
        audit("token_create_failed", request, target=name, details=str(e))
        return HTMLResponse(f'<div class="err">✗ Ошибка: {e}</div>', status_code=400)


@app.post("/tokens/{token_id}/revoke")
async def revoke_token_route(request: Request, token_id: int):
    require_login(request)
    if revoke_token(token_id):
        audit("token_revoked", request, target=str(token_id))
        return HTMLResponse("")
    raise HTTPException(status_code=404, detail="Токен не найден")


# ============================================================
# АУДИТ
# ============================================================

@app.get("/audit", response_class=HTMLResponse)
async def audit_page(request: Request, limit: int = 100):
    require_login(request)
    logs = recent(limit)
    return templates.TemplateResponse(request, "audit.html", {"logs": logs, "limit": limit})


# ============================================================
# КЭШ
# ============================================================

def _find_entity_config(alias: str) -> dict | None:
    """Поиск конфигурации сущности по alias"""
    for item in config.allowlist:
        if item.get("alias") == alias:
            return item
    return None


@app.get("/cache", response_class=HTMLResponse)
async def cache_page(request: Request):
    require_login(request)

    cache_status = []
    for item in config.allowlist:
        alias = item.get("alias")
        name = item.get("name")

        # Явная проверка обязательных полей (для типизатора)
        if not alias or not name:
            continue

        is_view = item.get("is_view", False)
        cache_mode = item.get("cache_mode", "full")

        # Получаем информацию из кэша
        df = cache_storage.load_entity(alias)
        last_updated = cache_storage.get_last_updated(alias)

        # Размер Parquet-файла
        parquet_path = cache_storage._get_parquet_path(alias)
        file_size = parquet_path.stat().st_size if parquet_path.exists() else 0

        cache_status.append({
            "alias": alias,
            "name": name,
            "description": item.get("description", ""),
            "row_count": len(df) if df is not None else 0,
            "last_updated": last_updated.isoformat() if last_updated else None,
            "file_size_mb": round(file_size / (1024 * 1024), 2) if file_size > 0 else 0,
            "cached": df is not None and len(df) > 0,
            "is_view": is_view,
            "cache_mode": cache_mode,
        })

    return templates.TemplateResponse(request, "cache.html", {"cache_status": cache_status})


@app.post("/cache/{alias}/refresh")
async def refresh_cache_route(request: Request, alias: str, mode: str = Form("full")):
    """Полное обновление одной сущности (с учётом cache_mode)"""
    require_login(request)

    entity_config = _find_entity_config(alias)
    if not entity_config:
        raise HTTPException(status_code=404, detail="Сущность не найдена")

    is_view = entity_config.get("is_view", False)
    cache_mode = entity_config.get("cache_mode", "full")
    full_name = entity_config.get("name")

    # Явная проверка (для типизатора)
    if not full_name:
        raise HTTPException(
            status_code=400, detail="У сущности отсутствует имя 1С")

    # Защита: не даём запускать обновление для неподходящих типов
    if is_view:
        return HTMLResponse('<div class="err">✗ SQL views пересоздаются автоматически</div>', status_code=400)
    if cache_mode == "derived":
        return HTMLResponse('<div class="err">✗ Производные витрины пересчитываются автоматически</div>', status_code=400)
    if cache_mode == "filtered_only":
        return HTMLResponse('<div class="err">✗ filtered_only сущности не кэшируются целиком</div>', status_code=400)

    audit("cache_refresh_started", request,
          target=alias, details=f"mode={cache_mode}")

    async def update_task():
        try:
            stats = await cache_scheduler.full_update(alias, full_name, cache_mode)
            details = f"mode={cache_mode}, batches={stats['batches']}, records={stats['records']}, status=success"
            audit("cache_refresh_completed", request,
                  target=alias, details=details)
        except Exception as e:
            logger.error(
                f"Ошибка фонового обновления {alias}: {e}", exc_info=True)
            audit("cache_refresh_failed", request, target=alias,
                  details=f"mode={cache_mode}, error={str(e)[:200]}")

    asyncio.create_task(update_task())
    return HTMLResponse('<div class="badge" style="background:#dbeafe;color:#1e40af">⏳ Полное обновление запущено</div>')


@app.post("/cache/{alias}/incremental")
async def refresh_cache_incremental(request: Request, alias: str):
    """Инкрементальное обновление одной сущности"""
    require_login(request)

    entity_config = _find_entity_config(alias)
    if not entity_config:
        raise HTTPException(status_code=404, detail="Сущность не найдена")

    is_view = entity_config.get("is_view", False)
    cache_mode = entity_config.get("cache_mode", "full")
    full_name = entity_config.get("name")

    # Явная проверка (для типизатора)
    if not full_name:
        raise HTTPException(
            status_code=400, detail="У сущности отсутствует имя 1С")

    # Защита: инкремент имеет смысл только для cache_mode=full
    if is_view:
        return HTMLResponse('<div class="err">✗ SQL views не поддерживают инкремент</div>', status_code=400)
    if cache_mode == "derived":
        return HTMLResponse('<div class="err">✗ Производные витрины пересчитываются автоматически</div>', status_code=400)
    if cache_mode == "filtered_only":
        return HTMLResponse('<div class="err">✗ filtered_only сущности не кэшируются</div>', status_code=400)
    if cache_mode == "slice_last":
        return HTMLResponse('<div class="err">✗ Для остатков используйте полное обновление (SliceLast)</div>', status_code=400)

    audit("cache_incremental_started", request, target=alias)

    async def update_task():
        try:
            stats = await cache_scheduler.incremental_update(alias, full_name)
            details = f"mode=incremental, batches={stats['batches']}, new_records={stats['records']}, total={stats['updated']}, status=success"
            audit("cache_incremental_completed",
                  request, target=alias, details=details)
        except Exception as e:
            logger.error(
                f"Ошибка фонового инкрементального обновления {alias}: {e}", exc_info=True)
            audit("cache_incremental_failed", request,
                  target=alias, details=f"error={str(e)[:200]}")

    asyncio.create_task(update_task())
    return HTMLResponse('<div class="badge" style="background:#dcfce7;color:#166534">⏳ Инкремент запущен</div>')


@app.post("/cache/refresh-all")
async def refresh_all_cache_route(request: Request, mode: str = Form("full")):
    require_login(request)
    audit("cache_refresh_all_started", request, details=f"mode={mode}")

    async def update_all_task():
        try:
            if mode == 'full':
                await cache_scheduler.full_update_all()
            else:
                await cache_scheduler.incremental_update_all()
            audit("cache_refresh_all_completed", request,
                  details=f"mode={mode}, status=success (см. логи для деталей по сущностям)")
        except Exception as e:
            logger.error(
                f"Ошибка фонового обновления всех сущностей: {e}", exc_info=True)
            audit("cache_refresh_all_failed", request,
                  details=f"error={str(e)[:200]}")
    asyncio.create_task(update_all_task())
    return HTMLResponse('<span style="color:#166534">✓ Обновление всего кэша запущено</span>')


@app.get("/cache/export")
async def export_cache(request: Request, alias: str):
    require_login(request)

    df = cache_storage.load_entity(alias)
    if df is None or len(df) == 0:
        return Response(content="Нет данных для экспорта", media_type="text/plain")

    output = io.StringIO()
    df.to_csv(output, index=False, encoding="utf-8-sig")

    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={alias}_cache.csv"}
    )


# ============================================================
# РОЛИ
# ============================================================

@app.get("/roles", response_class=HTMLResponse)
async def roles_page(request: Request):
    require_login(request)
    roles_info = []
    for role_name, role_config in role_registry.roles.items():
        roles_info.append({
            "name": role_name,
            "description": role_config.get("description", ""),
            "tools": role_config.get("tools", []),
            "entities": sorted(role_registry.effective_entities(role_name)),
            "max_rows": role_config.get("max_rows", 0)
        })
    return templates.TemplateResponse(request, "roles.html", {"roles": roles_info})

# ============================================================
# ЛОГИ
# ============================================================


@app.get("/logs", response_class=HTMLResponse)
async def logs_page(
    request: Request,
    limit: int = 100,
    tool: str | None = None,
    token_name: str | None = None,
    status: str | None = None,
):
    require_login(request)
    logs = recent_logs(limit=limit, tool=tool, token_name=token_name)
    if status:
        logs = [l for l in logs if l["status"] == status]

    all_logs = recent_logs(limit=1000)
    unique_tools = sorted({l["tool"] for l in all_logs if l["tool"]})
    unique_tokens = sorted({l["token_name"]
                           for l in all_logs if l["token_name"]})

    return templates.TemplateResponse(request, "logs.html", {
        "logs": logs,
        "limit": limit,
        "tool": tool,
        "token_name": token_name,
        "status": status,
        "unique_tools": unique_tools,
        "unique_tokens": unique_tokens,
    })


@app.get("/logs/stats", response_class=HTMLResponse)
async def logs_stats_page(request: Request, days: int = 7):
    require_login(request)
    stats_data = logs_stats(days=days)
    return templates.TemplateResponse(request, "logs_stats.html", {
        "stats": stats_data,
        "days": days,
    })


@app.post("/logs/cleanup")
async def logs_cleanup_route(request: Request, days: int = Form(30)):
    require_login(request)
    deleted = logs_cleanup(older_than_days=days)
    audit("logs_cleanup", request,
          details=f"deleted={deleted}, older_than={days}d")
    return HTMLResponse(f'<div style="color:#166534">✓ Удалено записей: {deleted}</div>')


@app.get("/logs/export")
async def export_logs(
    request: Request,
    tool: str | None = None,
    token_name: str | None = None,
    status: str | None = None,
):
    require_login(request)

    logs = recent_logs(limit=10000, tool=tool, token_name=token_name)
    if status:
        logs = [l for l in logs if l["status"] == status]

    output = io.StringIO()
    fieldnames = ["id", "ts", "token_name", "role", "tool",
                  "entity", "status", "result_summary", "duration_ms", "error"]
    writer = csv.DictWriter(output, fieldnames=fieldnames)

    writer.writeheader()
    for log in logs:
        row = {k: v for k, v in log.items() if k in fieldnames}
        writer.writerow(row)

    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=llm_logs.csv"}
    )


# ============================================================
# RATE LIMITS
# ============================================================

@app.get("/limits", response_class=HTMLResponse)
async def limits_page(request: Request):
    require_login(request)

    limits_info = rate_limiter.get_limits_info()
    counters = get_counters(limit=100)

    return templates.TemplateResponse(request, "limits.html", {
        "limits_info": limits_info,
        "counters": counters,
    })


@app.post("/limits/reset/{token_name}")
async def reset_limit_route(request: Request, token_name: str):
    require_login(request)
    deleted = reset_counter(token_name)
    audit("rate_limit_reset", request, target=token_name,
          details=f"deleted={deleted}")
    return HTMLResponse(f'<span style="color:#166534">✓ Сброшено</span>')


@app.post("/limits/cleanup")
async def cleanup_limits_route(request: Request, windows: int = Form(10)):
    require_login(request)
    deleted = cleanup_counters(older_than_windows=windows)
    audit("rate_limit_cleanup", request,
          details=f"deleted={deleted}, older_than={windows} windows")
    return HTMLResponse(f'<div style="color:#166534">✓ Удалено записей: {deleted}</div>')


# ============================================================
# VIEWS (Скомпилированные представления)
# ============================================================

@app.get("/views", response_class=HTMLResponse)
async def views_page(request: Request):
    require_login(request)

    configured_views = [e for e in config.allowlist if e.get("is_view")]
    cached_entities = cache_storage.list_cached_entities()
    cached_dict = {e["alias"]: e for e in cached_entities if e.get("is_view")}

    view_status = []
    for v in configured_views:
        alias = v["alias"]
        cached = cached_dict.get(alias)
        last_updated = cached["last_updated"] if cached else None
        view_status.append({
            "alias": alias,
            "name": v.get("name"),
            "description": v.get("description"),
            "row_count": cached["row_count"] if cached else 0,
            "last_updated": last_updated.isoformat() if last_updated else None,  # <-- ИСПРАВЛЕНО
            "exists": cached is not None
        })

    return templates.TemplateResponse(request, "views.html", {"views": view_status})


@app.post("/views/recreate")
async def recreate_views(request: Request):
    require_login(request)

    configured_views = [e for e in config.allowlist if e.get("is_view")]
    results = cache_storage.create_views(configured_views)

    success = sum(1 for v in results.values() if v)
    total = len(results)

    audit("views_recreate", request,
          details=f"success={success}, total={total}")
    return HTMLResponse(f'<div style="color:#166534; font-weight:500">✓ Views пересозданы: успешно {success}/{total}</div>')
# === РАЗДЕЛ DERIVED (Производные витрины: BOM, прогнозы, потребности) ===


@app.get("/derived", response_class=HTMLResponse)
async def derived_page(request: Request):
    require_login(request)

    derived_entities = [e for e in config.allowlist if e.get(
        "cache_mode") == "derived"]
    cached_entities = cache_storage.list_cached_entities()
    cached_dict = {e["alias"]: e for e in cached_entities}

    derived_status = []
    for entity in derived_entities:
        alias = entity["alias"]
        cached = cached_dict.get(alias)
        last_updated = cached["last_updated"] if cached else None
        derived_status.append({
            "alias": alias,
            "name": entity.get("name"),
            "description": entity.get("description"),
            "row_count": cached["row_count"] if cached else 0,
            "last_updated": last_updated.isoformat() if last_updated else None,  # <-- ИСПРАВЛЕНО
            "exists": cached is not None
        })

    return templates.TemplateResponse(request, "derived.html", {
        "derived_status": derived_status
    })


@app.post("/derived/rebuild")
async def rebuild_derived(request: Request):
    require_login(request)
    audit("derived_rebuild_started", request)

    async def rebuild_task():
        import asyncio
        import concurrent.futures

        try:
            loop = asyncio.get_event_loop()
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                # BOM в отдельном потоке
                logger.info("🔨 Построение BOM...")
                bom_results = await loop.run_in_executor(
                    executor, bom_builder.build_all
                )
                bom_ok = sum(1 for v in bom_results.values() if v)
                logger.info(
                    f"✓ BOM построен: {bom_ok}/{len(bom_results)} витрин")

                # Прогноз в отдельном потоке
                logger.info("📊 Построение витрин прогноза...")
                forecast_results = await loop.run_in_executor(
                    executor, forecast_builder.build_all
                )
                forecast_ok = sum(1 for v in forecast_results.values() if v)
                logger.info(
                    f"✓ Прогноз построен: {forecast_ok}/{len(forecast_results)} витрин")

            audit("derived_rebuild_completed",
                  request, details="status=success")
        except Exception as e:
            logger.error(
                f"Ошибка пересчёта derived-витрин: {e}", exc_info=True)
            audit("derived_rebuild_failed", request, details=str(e)[:200])

    asyncio.create_task(rebuild_task())
    return HTMLResponse('<div style="color:#166534; font-weight:500">⏳ Пересчёт запущен в фоне</div>')
# === РАЗДЕЛ SCHEDULE (Расписание синхронизации) ===


@app.get("/schedule", response_class=HTMLResponse)
async def schedule_page(request: Request):
    require_login(request)
    from mcp_1c_odata.cache.schedule_storage import list_jobs

    jobs = cache_scheduler.get_jobs_info()

    # Список всех сущностей для выбора
    all_entities = [
        {"alias": e["alias"], "name": e["name"],
            "description": e.get("description", "")}
        for e in config.allowlist
        if not e.get("is_view") and e.get("cache_mode") != "derived"
    ]

    return templates.TemplateResponse(request, "schedule.html", {
        "jobs": jobs,
        "all_entities": all_entities,
    })


@app.post("/schedule/create")
async def schedule_create(
    request: Request,
    name: str = Form(...),
    cron_expr: str = Form(...),
    job_type: str = Form(...),
    entities: str = Form(""),  # JSON-массив или пусто для "all"
    enabled: bool = Form(False),
):
    require_login(request)
    from mcp_1c_odata.cache.schedule_storage import create_job

    # Валидация cron
    try:
        from apscheduler.triggers.cron import CronTrigger
        CronTrigger.from_crontab(cron_expr)
    except Exception as e:
        return HTMLResponse(f'<div class="err">✗ Неверное cron-выражение: {e}</div>', status_code=400)

    # Валидация job_type
    if job_type not in ("full", "incremental"):
        return HTMLResponse('<div class="err">✗ Неверный тип задания</div>', status_code=400)

    # Обработка entities
    entities_json = entities.strip() if entities.strip() else None

    try:
        job_id = create_job(name, cron_expr, job_type, entities_json, enabled)
        audit("schedule_created", request, target=name,
              details=f"cron={cron_expr}, type={job_type}")

        if enabled:
            cache_scheduler.resync_jobs()

        return HTMLResponse(f'<div style="color:#166534">✓ Задание создано (ID: {job_id})</div>')
    except Exception as e:
        return HTMLResponse(f'<div class="err">✗ Ошибка: {e}</div>', status_code=400)


@app.post("/schedule/{job_id}/update")
async def schedule_update(
    request: Request,
    job_id: int,
    name: str = Form(...),
    cron_expr: str = Form(...),
    job_type: str = Form(...),
    entities: str = Form(""),
    enabled: bool = Form(False),
):
    require_login(request)
    from mcp_1c_odata.cache.schedule_storage import update_job

    try:
        from apscheduler.triggers.cron import CronTrigger
        CronTrigger.from_crontab(cron_expr)
    except Exception as e:
        return HTMLResponse(f'<div class="err">✗ Неверное cron-выражение: {e}</div>', status_code=400)

    entities_json = entities.strip() if entities.strip() else None

    try:
        update_job(
            job_id,
            name=name,
            cron_expr=cron_expr,
            job_type=job_type,
            entities=entities_json,
            enabled=enabled,
        )
        audit("schedule_updated", request, target=str(
            job_id), details=f"cron={cron_expr}")
        cache_scheduler.resync_jobs()
        return HTMLResponse('<div style="color:#166534">✓ Задание обновлено</div>')
    except Exception as e:
        return HTMLResponse(f'<div class="err">✗ Ошибка: {e}</div>', status_code=400)


@app.post("/schedule/{job_id}/delete")
async def schedule_delete(request: Request, job_id: int):
    require_login(request)
    from mcp_1c_odata.cache.schedule_storage import delete_job

    if delete_job(job_id):
        audit("schedule_deleted", request, target=str(job_id))
        cache_scheduler.resync_jobs()
        return HTMLResponse('<div style="color:#166534">✓ Удалено</div>')
    return HTMLResponse('<div class="err">✗ Не найдено</div>', status_code=404)


@app.post("/schedule/{job_id}/toggle")
async def schedule_toggle(request: Request, job_id: int):
    require_login(request)
    from mcp_1c_odata.cache.schedule_storage import get_job, toggle_job

    job = get_job(job_id)
    if not job:
        return HTMLResponse('<div class="err">✗ Не найдено</div>', status_code=404)

    new_state = not bool(job["enabled"])
    toggle_job(job_id, new_state)
    audit("schedule_toggled", request, target=str(
        job_id), details=f"enabled={new_state}")
    cache_scheduler.resync_jobs()
    return HTMLResponse(f'<div style="color:#166534">✓ {"Включено" if new_state else "Отключено"}</div>')


@app.post("/schedule/{job_id}/run-now")
async def schedule_run_now(request: Request, job_id: int):
    require_login(request)
    from mcp_1c_odata.cache.schedule_storage import get_job

    job = get_job(job_id)
    if not job:
        return HTMLResponse('<div class="err">✗ Не найдено</div>', status_code=404)

    # Запускаем задание немедленно в фоне
    async def run_task():
        try:
            if job["job_type"] == "full":
                if job["entities"]:
                    import json
                    for alias in json.loads(job["entities"]):
                        entity_config = next(
                            (e for e in config.allowlist if e.get(
                                "alias") == alias), None
                        )
                        if entity_config:
                            full_name = entity_config.get("name")
                            cache_mode = entity_config.get(
                                "cache_mode", "full")

                            # Явная проверка для типизатора и защиты от битых конфигов
                            if not full_name:
                                logger.warning(
                                    f"⚠️ Пропуск сущности '{alias}': в конфигурации отсутствует поле 'name'")
                                continue

                            await cache_scheduler.full_update(alias, full_name, cache_mode)
                else:
                    await cache_scheduler.full_update_all()
            else:
                if job["entities"]:
                    import json
                    for alias in json.loads(job["entities"]):
                        entity_config = next(
                            (e for e in config.allowlist if e.get(
                                "alias") == alias), None
                        )
                        if entity_config:
                            full_name = entity_config.get("name")

                            # Явная проверка для типизатора и защиты от битых конфигов
                            if not full_name:
                                logger.warning(
                                    f"⚠️ Пропуск сущности '{alias}': в конфигурации отсутствует поле 'name'")
                                continue

                            await cache_scheduler.incremental_update(alias, full_name)
                else:
                    await cache_scheduler.incremental_update_all()
        except Exception as e:
            logger.error(
                f"Ошибка ручного запуска задания {job['name']}: {e}", exc_info=True)

    asyncio.create_task(run_task())
    audit("schedule_run_now", request, target=job["name"])
    return HTMLResponse('<div style="color:#166534">⏳ Запущено</div>')

# ============================================================
# ЗАПУСК
# ============================================================


@click.command()
@click.option("--host", default=cfg.DEFAULT_HOST)
@click.option("--port", default=cfg.DEFAULT_PORT, type=int)
def run(host: str, port: int):
    if not cfg.ADMIN_PASSWORD:
        raise SystemExit(
            "Ошибка: не задан MCP_ADMIN_PASSWORD. Установите переменную окружения.")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    run()
