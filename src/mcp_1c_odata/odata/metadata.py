import xml.etree.ElementTree as ET
import httpx
from mcp_1c_odata.config import config


class MetadataParser:
    def __init__(self):
        self._cache = {}

    async def get_metadata(self) -> dict:
        if "metadata" not in self._cache:
            url = f"{config.odata_base_url.rstrip('/')}/$metadata"
            auth = (config.odata_user,
                    config.odata_password) if config.odata_user else None

            try:
                async with httpx.AsyncClient(auth=auth, timeout=config.odata_timeout_sec) as client:
                    response = await client.get(url)
                    response.raise_for_status()
                    self._cache["metadata"] = self._parse_xml(response.text)
            except Exception as e:
                self._cache["metadata"] = {
                    "error": f"Ошибка получения метаданных: {str(e)}"}

        return self._cache["metadata"]

    def _parse_xml(self, xml_content: str) -> dict:
        try:
            root = ET.fromstring(xml_content)
            # 1C OData v3 использует пространство имен Microsoft
            ns = {'edm': 'http://schemas.microsoft.com/ado/2009/11/edm'}

            entities = {}
            for entity_type in root.findall('.//edm:EntityType', ns):
                name = entity_type.get('Name')
                properties = []
                for prop in entity_type.findall('edm:Property', ns):
                    properties.append({
                        'name': prop.get('Name'),
                        'type': prop.get('Type')
                    })
                entities[name] = {
                    'name': name,
                    'properties': properties
                }
            return entities
        except ET.ParseError as e:
            return {"error": f"Ошибка парсинга XML: {str(e)}"}


metadata_parser = MetadataParser()
