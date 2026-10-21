from mcp_1c_odata.config import config

views = [e for e in config.allowlist if e.get(
    'is_view') or (e.get('name') or '').startswith('View_')]
print(f'Найдено view в конфиге: {len(views)}')
for v in views:
    print(f"  - {v.get('alias')}: is_view={v.get('is_view')}, name={v.get('name')}")
