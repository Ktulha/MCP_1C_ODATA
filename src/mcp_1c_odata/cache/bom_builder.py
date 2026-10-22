import logging
import pandas as pd
from typing import Optional
from mcp_1c_odata.config import config
from mcp_1c_odata.cache.storage import cache_storage

logger = logging.getLogger("mcp-1c-odata.bom")


class BOMBuilder:
    """Построитель спецификаций (Bill of Materials) с рекурсией и расчётом себестоимости."""

    def __init__(self):
        bom_config: dict = config.forecast.get(
            "bom", {}) if isinstance(config.forecast, dict) else {}
        self.max_depth: int = bom_config.get("max_depth", 10)
        self.include_operations: bool = bom_config.get(
            "include_operations", True)
        self.save_tree: bool = bom_config.get("save_tree", True)
        self.save_flat: bool = bom_config.get("save_flat", True)

    def build_all(self) -> dict[str, bool]:
        """Построение всех BOM-витрин."""
        results: dict[str, bool] = {}

        try:
            logger.info("Загрузка данных для построения BOM...")
            spec_comp_df = self._load_parquet("spec_composition")
            spec_ops_df = self._load_parquet("spec_operations")
            default_spec_df = self._load_parquet("default_spec")
            nomenclature_df = self._load_parquet("nomenclature")
            variants_df = self._load_parquet("variants")
            prices_df = self._load_parquet("prices_last")

            if spec_comp_df is None or default_spec_df is None:
                logger.warning(
                    "Недостаточно данных для построения BOM (spec_composition или default_spec отсутствуют)")
                return {"bom_tree": False, "bom_flat": False}

            nomenclature_lookup = self._create_lookup(
                nomenclature_df, ["Ref_Key", "Description"])
            variants_lookup = self._create_lookup(
                variants_df, ["Ref_Key", "Description"])
            prices_lookup = self._create_latest_prices(prices_df)

            if self.save_tree:
                bom_tree_df = self._build_bom_tree(
                    spec_comp_df, spec_ops_df, default_spec_df,
                    nomenclature_lookup, variants_lookup, prices_lookup
                )
                if bom_tree_df is not None and len(bom_tree_df) > 0:
                    cache_storage.save_entity(
                        "bom_tree", bom_tree_df, is_view=False)
                    logger.info(
                        f"✓ bom_tree построен: {len(bom_tree_df)} строк")
                    results["bom_tree"] = True
                else:
                    results["bom_tree"] = False

            if self.save_flat:
                bom_flat_df = self._build_bom_flat(
                    spec_comp_df, spec_ops_df, default_spec_df,
                    nomenclature_lookup, variants_lookup, prices_lookup
                )
                if bom_flat_df is not None and len(bom_flat_df) > 0:
                    cache_storage.save_entity(
                        "bom_flat", bom_flat_df, is_view=False)
                    logger.info(
                        f"✓ bom_flat построен: {len(bom_flat_df)} строк")
                    results["bom_flat"] = True
                else:
                    results["bom_flat"] = False

        except Exception as e:
            logger.error(f"Ошибка построения BOM: {e}", exc_info=True)
            results["bom_tree"] = False
            results["bom_flat"] = False

        return results

    def _load_parquet(self, alias: str) -> Optional[pd.DataFrame]:
        parquet_path = cache_storage._get_parquet_path(alias)
        if not parquet_path.exists():
            return None
        try:
            return pd.read_parquet(parquet_path)
        except Exception as e:
            logger.error(f"Ошибка загрузки {alias}: {e}")
            return None

    def _create_lookup(self, df: Optional[pd.DataFrame], columns: list[str]) -> dict:
        if df is None or len(df) == 0:
            return {}
        result: dict = {}
        for _, row in df.iterrows():
            key = row.get("Ref_Key")
            if key:
                result[key] = {col: row.get(col)
                               for col in columns if col != "Ref_Key"}
        return result

    def _create_latest_prices(self, prices_df: Optional[pd.DataFrame]) -> dict:
        if prices_df is None or len(prices_df) == 0:
            return {}
        prices_df = prices_df.sort_values("Period", ascending=False)
        latest_prices = prices_df.drop_duplicates(
            subset=["Номенклатура_Key"], keep="first")
        result: dict = {}
        for _, row in latest_prices.iterrows():
            nomenclature_ref = row.get("Номенклатура_Key")
            if nomenclature_ref:
                result[nomenclature_ref] = row.get("Цена", 0.0)
        return result

    def _build_bom_tree(
        self,
        spec_comp_df: pd.DataFrame,
        spec_ops_df: Optional[pd.DataFrame],
        default_spec_df: pd.DataFrame,
        nomenclature_lookup: dict,
        variants_lookup: dict,
        prices_lookup: dict
    ) -> Optional[pd.DataFrame]:
        """Построение иерархического дерева BOM с рекурсией."""

        # Создаём lookup спецификаций: Номенклатура_Key -> Спецификация_Key
        spec_lookup: dict = {}
        for _, row in default_spec_df.iterrows():
            nomenclature_ref = row.get("Номенклатура_Key")
            specification_ref = row.get("Спецификация_Key")
            if nomenclature_ref and specification_ref:
                spec_lookup[nomenclature_ref] = specification_ref

        # Создаём lookup состава спецификаций: Ref_Key (спецификации) -> list of components
        spec_composition_lookup: dict = {}
        for _, row in spec_comp_df.iterrows():
            # Ref_Key в spec_composition = ключ спецификации
            spec_ref = row.get("Ref_Key")
            if spec_ref:
                if spec_ref not in spec_composition_lookup:
                    spec_composition_lookup[spec_ref] = []
                spec_composition_lookup[spec_ref].append(row)

        # Создаём lookup операций спецификаций
        spec_operations_lookup: dict = {}
        if spec_ops_df is not None and len(spec_ops_df) > 0:
            for _, row in spec_ops_df.iterrows():
                spec_ref = row.get("Ref_Key")
                if spec_ref:
                    if spec_ref not in spec_operations_lookup:
                        spec_operations_lookup[spec_ref] = []
                    spec_operations_lookup[spec_ref].append(row)

        # Рекурсивное построение дерева
        tree_rows: list[dict] = []
        visited: set = set()

        def recurse(product_ref: str, level: int, parent_path: str):
            if level > self.max_depth:
                return
            if product_ref in visited:
                logger.warning(f"Обнаружен цикл в BOM: {product_ref}")
                return

            visited.add(product_ref)

            specification_ref = spec_lookup.get(product_ref)
            if not specification_ref:
                return

            current_path = f"{parent_path}/{product_ref}" if parent_path else product_ref

            # Добавляем компоненты
            components = spec_composition_lookup.get(specification_ref, [])
            for comp in components:
                component_ref = comp.get("Номенклатура_Key")
                if not component_ref:
                    continue

                quantity = comp.get("Количество", 0.0)
                quantity_product = comp.get("КоличествоПродукции", 1.0)
                # Норма на 1 единицу продукции
                norm_per_unit = quantity / quantity_product if quantity_product > 0 else quantity

                component_info = nomenclature_lookup.get(component_ref, {})
                variant_info = variants_lookup.get(
                    comp.get("Характеристика_Key"), {})

                tree_rows.append({
                    "product_ref": product_ref,
                    "product_name": nomenclature_lookup.get(product_ref, {}).get("Description"),
                    "component_ref": component_ref,
                    "component_name": component_info.get("Description"),
                    "variant_name": variant_info.get("Description"),
                    "level": level,
                    "path": current_path,
                    "quantity_per_unit": norm_per_unit,
                    "is_operation": False,
                    "cost": prices_lookup.get(component_ref, 0.0) * norm_per_unit,
                })

                # Рекурсия, если компонент тоже является продукцией
                if component_ref in spec_lookup:
                    recurse(component_ref, level + 1, current_path)

            # Добавляем операции
            if self.include_operations:
                operations = spec_operations_lookup.get(specification_ref, [])
                for op in operations:
                    operation_ref = op.get("Операция_Key")
                    norm_time = op.get("НормаВремени", 0.0)
                    description = op.get(
                        "Описание", f"Операция {op.get('LineNumber')}")

                    tree_rows.append({
                        "product_ref": product_ref,
                        "product_name": nomenclature_lookup.get(product_ref, {}).get("Description"),
                        "component_ref": operation_ref or f"op_{specification_ref}_{op.get('LineNumber')}",
                        "component_name": description,
                        "variant_name": None,
                        "level": level,
                        "path": current_path,
                        "quantity_per_unit": norm_time,
                        "is_operation": True,
                        "cost": 0.0,  # Стоимость операций считаем отдельно
                    })

            visited.remove(product_ref)

        # Начинаем рекурсию для всех продуктов, у которых есть спецификация
        for product_ref in spec_lookup.keys():
            recurse(product_ref, 0, "")

        return pd.DataFrame(tree_rows) if tree_rows else None

    def _build_bom_flat(
        self,
        spec_comp_df: pd.DataFrame,
        spec_ops_df: Optional[pd.DataFrame],
        default_spec_df: pd.DataFrame,
        nomenclature_lookup: dict,
        variants_lookup: dict,
        prices_lookup: dict
    ) -> Optional[pd.DataFrame]:
        """Построение плоской таблицы BOM (все компоненты на всех уровнях)."""

        tree_df = self._build_bom_tree(
            spec_comp_df, spec_ops_df, default_spec_df,
            nomenclature_lookup, variants_lookup, prices_lookup
        )

        if tree_df is None or len(tree_df) == 0:
            return None

        # Группируем по корневой продукции и компоненту, суммируем количества
        flat_rows: list[dict] = []

        for (product_ref, component_ref), group in tree_df.groupby(["product_ref", "component_ref"]):
            if len(group) == 0:
                continue

            first_row = group.iloc[0]
            total_quantity = group["quantity_per_unit"].sum()
            total_cost = group["cost"].sum()
            max_level = group["level"].max()

            flat_rows.append({
                "product_ref": product_ref,
                "product_name": first_row["product_name"],
                "component_ref": component_ref,
                "component_name": first_row["component_name"],
                "variant_name": first_row["variant_name"],
                "max_level": max_level,
                "quantity_per_unit": total_quantity,
                "is_operation": first_row["is_operation"],
                "cost": total_cost,
            })

        return pd.DataFrame(flat_rows) if flat_rows else None


bom_builder = BOMBuilder()
