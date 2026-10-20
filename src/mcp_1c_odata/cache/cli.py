import asyncio
import click
from mcp_1c_odata.cache.scheduler import cache_scheduler
from mcp_1c_odata.config import config


@click.group()
def cli():
    """Управление кэшем MCP-сервера"""
    pass


@cli.command()
@click.option('--entity', '-e', help='Алиас сущности (пусто = все)')
@click.option('--mode', '-m', default='full', type=click.Choice(['full', 'incremental']), help='Режим обновления')
def refresh(entity: str | None, mode: str):
    """Принудительное обновление кэша"""

    async def run():
        if entity:
            # Находим полное имя по алиасу
            full_name = None
            for item in config.allowlist:
                if item.get("alias") == entity:
                    full_name = item.get("name")
                    break

            if not full_name:
                click.echo(
                    f"✗ Сущность '{entity}' не найдена в allowlist", err=True)
                return

            click.echo(f"Запуск {mode} обновления для '{entity}'...")
            if mode == 'full':
                await cache_scheduler.full_update(entity, full_name)
            else:
                await cache_scheduler.incremental_update(entity, full_name)
        else:
            click.echo(f"Запуск {mode} обновления всех сущностей...")
            if mode == 'full':
                await cache_scheduler.full_update_all()
            else:
                await cache_scheduler.incremental_update_all()

        click.echo("✓ Готово")

    asyncio.run(run())


@cli.command()
def status():
    """Показать статус кэша"""
    from mcp_1c_odata.cache.storage import cache_storage

    click.echo(
        f"{'Алиас':<20} {'Записей':<12} {'Размер':<12} {'Обновлено':<20}")
    click.echo("-" * 70)

    for item in config.allowlist:
        alias = item.get("alias")
        df = cache_storage.load_entity(alias)
        last_updated = cache_storage.get_last_updated(alias)

        row_count = len(df) if df is not None else 0
        updated_str = last_updated.strftime(
            "%Y-%m-%d %H:%M:%S") if last_updated else "—"

        # Размер файла
        parquet_path = cache_storage._get_parquet_path(alias)
        if parquet_path.exists():
            size_mb = parquet_path.stat().st_size / (1024 * 1024)
            size_str = f"{size_mb:.2f} MB"
        else:
            size_str = "—"

        click.echo(
            f"{alias:<20} {row_count:<12} {size_str:<12} {updated_str:<20}")


if __name__ == '__main__':
    cli()
