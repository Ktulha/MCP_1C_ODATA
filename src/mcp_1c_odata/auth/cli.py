import click
from mcp_1c_odata.auth.db import init_db, create_token, list_tokens, revoke_token, cleanup_expired
from mcp_1c_odata.auth.roles import role_registry


@click.group()
def cli():
    """Управление токенами MCP-сервера"""
    init_db()


@cli.command()
@click.option('--name', required=True, help='Имя токена')
@click.option('--scope', default='', help='Сущности через запятую (пусто = все по роли)')
@click.option('--role', default='viewer', help='Роль: viewer | analyst | admin')
@click.option('--days', default=30, help='Срок действия в днях')
def create(name: str, scope: str, role: str, days: int):
    """Создать новый токен"""
    scope_list = [s.strip() for s in scope.split(
        ",") if s.strip()] if scope else None
    try:
        final_scope = role_registry.validate_scope(role, scope_list)
        raw_token = create_token(name, ",".join(
            final_scope), role=role, days=days)
        click.echo(f"✓ Токен создан")
        click.echo(f"  Имя:   {name}")
        click.echo(f"  Роль:  {role}")
        click.echo(f"  Scope: {', '.join(final_scope) or '(все по роли)'}")
        click.echo(f"  Срок:  {days} дней")
        click.echo(f"  Токен: {raw_token}")
        click.echo("\n⚠ Сохраните токен — он показан только один раз!")
    except ValueError as e:
        click.echo(f"✗ Ошибка: {e}", err=True)


@cli.command('list')
def list_cmd():
    """Список всех токенов"""
    tokens = list_tokens()
    if not tokens:
        click.echo("Токенов нет")
        return

    click.echo(
        f"{'ID':<5} {'Имя':<20} {'Роль':<10} {'Scope':<30} {'Активен':<8} {'Истекает':<20}")
    click.echo("-" * 100)
    for t in tokens:
        status = "✓" if t["active"] else "✗"
        click.echo(
            f"{t['id']:<5} {t['name']:<20} {t['role']:<10} {t['scope']:<30} {status:<8} {t['expires_at']:<20}")


@cli.command()
@click.argument('token_id', type=int)
def revoke(token_id: int):
    """Отозвать токен по ID"""
    if revoke_token(token_id):
        click.echo(f"✓ Токен {token_id} отозван")
    else:
        click.echo(f"✗ Токен {token_id} не найден", err=True)


@cli.command()
def cleanup():
    """Удалить просроченные токены"""
    deleted = cleanup_expired()
    click.echo(f"✓ Удалено просроченных токенов: {deleted}")


@cli.command('roles')
def roles_cmd():
    """Список доступных ролей"""
    for name, role in role_registry.roles.items():
        click.echo(
            f"{name:<10} max_rows={role.max_rows:<6} tools={list(role.tools)}  entities={list(role.entities)}")
        click.echo(f"           {role.description}")


if __name__ == '__main__':
    cli()
