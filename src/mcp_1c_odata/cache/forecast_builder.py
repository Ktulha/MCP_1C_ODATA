import logging
import pandas as pd
from typing import Optional
from datetime import datetime, timedelta
from mcp_1c_odata.config import config
from mcp_1c_odata.cache.storage import cache_storage

logger = logging.getLogger("mcp-1c-odata.forecast")


class ForecastBuilder:
    """Построитель витрин прогноза и планирования."""

    def __init__(self):
        self.horizons_days: list[int] = config.forecast.get(
            "horizons_days", [7, 14, 30, 90])
        self.default_horizon: int = config.forecast.get("default_horizon", 30)
        self.history_days: int = config.forecast.get("history_days", 90)

    def build_all(self) -> dict[str, bool]:
        results: dict[str, bool] = {}

        try:
            logger.info("📥 [1/8] Загрузка parquet-файлов...")
            sales_doc_df = self._load_parquet("sales_doc")
            sales_doc_lines_df = self._load_parquet("sales_doc_lines")
            purchase_doc_df = self._load_parquet("purchase_doc")
            purchase_doc_lines_df = self._load_parquet("purchase_doc_lines")
            stock_balance_df = self._load_parquet("stock_balance_info")
            bom_flat_df = self._load_parquet("bom_flat")
            nomenclature_df = self._load_parquet("nomenclature")
            prices_df = self._load_parquet("prices_last")
            logger.info(f"   ✓ Загружено: sales_doc={len(sales_doc_df) if sales_doc_df is not None else 0}, "
                        f"sales_lines={len(sales_doc_lines_df) if sales_doc_lines_df is not None else 0}, "
                        f"bom_flat={len(bom_flat_df) if bom_flat_df is not None else 0}")

            if sales_doc_df is None or sales_doc_lines_df is None:
                logger.warning(
                    "Недостаточно данных (sales_doc или sales_doc_lines отсутствуют)")
                return self._empty_results()

            logger.info("🔧 [2/8] Построение lookup-таблиц...")
            nomenclature_lookup = self._create_lookup(
                nomenclature_df, ["Ref_Key", "Description"])
            prices_lookup = self._create_latest_prices(prices_df)

            logger.info("📊 [3/8] Обороты продаж по месяцам...")
            sales_turnover_df = self._build_sales_turnover_monthly(
                sales_doc_df, sales_doc_lines_df, nomenclature_lookup)
            if sales_turnover_df is not None:
                cache_storage.save_entity(
                    "sales_turnover_monthly", sales_turnover_df, is_view=False)
                logger.info(
                    f"   ✓ sales_turnover_monthly: {len(sales_turnover_df)} строк")
                results["sales_turnover_monthly"] = True
            else:
                results["sales_turnover_monthly"] = False

            logger.info("📈 [4/8] Средний дневной расход...")
            avg_daily_df = self._build_avg_daily_consumption(sales_turnover_df)
            if avg_daily_df is not None:
                cache_storage.save_entity(
                    "avg_daily_consumption", avg_daily_df, is_view=False)
                logger.info(
                    f"   ✓ avg_daily_consumption: {len(avg_daily_df)} строк")
                results["avg_daily_consumption"] = True
            else:
                results["avg_daily_consumption"] = False

            logger.info("🔮 [5/8] Прогноз спроса...")
            forecast_df = self._build_forecast_demand(avg_daily_df)
            if forecast_df is not None:
                cache_storage.save_entity(
                    "forecast_demand", forecast_df, is_view=False)
                logger.info(f"   ✓ forecast_demand: {len(forecast_df)} строк")
                results["forecast_demand"] = True
            else:
                results["forecast_demand"] = False

            logger.info("🧮 [6/8] Потребность в материалах...")
            if bom_flat_df is not None:
                material_req_df = self._build_material_requirement(
                    forecast_df, bom_flat_df, nomenclature_lookup)
                if material_req_df is not None:
                    cache_storage.save_entity(
                        "material_requirement", material_req_df, is_view=False)
                    logger.info(
                        f"   ✓ material_requirement: {len(material_req_df)} строк")
                    results["material_requirement"] = True
                else:
                    results["material_requirement"] = False
            else:
                results["material_requirement"] = False

            logger.info("🛒 [7/8] Рекомендация к закупке + покрытие запасов...")
            if stock_balance_df is not None and bom_flat_df is not None:
                purchase_rec_df = self._build_purchase_recommendation(
                    material_req_df, stock_balance_df, prices_lookup, nomenclature_lookup
                )
                if purchase_rec_df is not None:
                    cache_storage.save_entity(
                        "purchase_recommendation", purchase_rec_df, is_view=False)
                    logger.info(
                        f"   ✓ purchase_recommendation: {len(purchase_rec_df)} строк")
                    results["purchase_recommendation"] = True
                else:
                    results["purchase_recommendation"] = False
            else:
                results["purchase_recommendation"] = False

            if stock_balance_df is not None and avg_daily_df is not None:
                coverage_df = self._build_stock_coverage(
                    stock_balance_df, avg_daily_df, nomenclature_lookup)
                if coverage_df is not None:
                    cache_storage.save_entity(
                        "stock_coverage", coverage_df, is_view=False)
                    logger.info(
                        f"   ✓ stock_coverage: {len(coverage_df)} строк")
                    results["stock_coverage"] = True
                else:
                    results["stock_coverage"] = False
            else:
                results["stock_coverage"] = False

            logger.info("💰 [8/8] Себестоимость + рентабельность...")
            if bom_flat_df is not None:
                cost_estimate_df = self._build_production_cost_estimate(
                    bom_flat_df, nomenclature_lookup)
                if cost_estimate_df is not None:
                    cache_storage.save_entity(
                        "production_cost_estimate", cost_estimate_df, is_view=False)
                    logger.info(
                        f"   ✓ production_cost_estimate: {len(cost_estimate_df)} строк")
                    results["production_cost_estimate"] = True
                else:
                    results["production_cost_estimate"] = False
            else:
                results["production_cost_estimate"] = False

            if purchase_doc_lines_df is not None and sales_doc_lines_df is not None:
                profitability_df = self._build_profitability_analysis(
                    sales_doc_lines_df, purchase_doc_lines_df, nomenclature_lookup
                )
                if profitability_df is not None:
                    cache_storage.save_entity(
                        "profitability_analysis", profitability_df, is_view=False)
                    logger.info(
                        f"   ✓ profitability_analysis: {len(profitability_df)} строк")
                    results["profitability_analysis"] = True
                else:
                    results["profitability_analysis"] = False
            else:
                results["profitability_analysis"] = False

            logger.info("✅ Построение витрин прогноза завершено")

        except Exception as e:
            logger.error(
                f"Ошибка построения витрин прогноза: {e}", exc_info=True)
            return self._empty_results()

        return results

    def _empty_results(self) -> dict[str, bool]:
        return {
            "sales_turnover_monthly": False,
            "avg_daily_consumption": False,
            "forecast_demand": False,
            "material_requirement": False,
            "purchase_recommendation": False,
            "stock_coverage": False,
            "production_cost_estimate": False,
            "profitability_analysis": False,
        }

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

    def _build_sales_turnover_monthly(
        self,
        sales_doc_df: pd.DataFrame,
        sales_doc_lines_df: pd.DataFrame,
        nomenclature_lookup: dict
    ) -> Optional[pd.DataFrame]:
        """Агрегированные отгрузки по месяцам."""
        rows: list[dict] = []

        for _, line in sales_doc_lines_df.iterrows():
            doc_ref = line.get("Ref_Key")
            doc_row = sales_doc_df[sales_doc_df["Ref_Key"] == doc_ref]

            if len(doc_row) == 0:
                continue

            doc = doc_row.iloc[0]
            if not doc.get("Posted", False) or doc.get("DeletionMark", False):
                continue

            nomenclature_ref = line.get("Номенклатура_Key")
            if not nomenclature_ref:
                continue

            quantity = line.get("Количество", 0.0)
            amount = line.get("Сумма", 0.0)
            date = doc.get("Date")

            if date is None or pd.isna(date):
                continue

            if isinstance(date, str):
                try:
                    date = pd.to_datetime(date)
                except:
                    continue

            month = date.replace(day=1).strftime("%Y-%m-%d")
            nomenclature_info = nomenclature_lookup.get(nomenclature_ref, {})

            rows.append({
                "month": month,
                "nomenclature_ref": nomenclature_ref,
                "nomenclature_name": nomenclature_info.get("Description"),
                "quantity": quantity,
                "amount": amount,
            })

        if not rows:
            return None

        df = pd.DataFrame(rows)
        grouped = df.groupby(["month", "nomenclature_ref", "nomenclature_name"]).agg({
            "quantity": "sum",
            "amount": "sum"
        }).reset_index()

        return grouped

    def _build_avg_daily_consumption(self, sales_turnover_df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
        if sales_turnover_df is None or len(sales_turnover_df) == 0:
            return None

        cutoff_date = datetime.now() - timedelta(days=self.history_days)
        sales_turnover_df = sales_turnover_df.copy()
        sales_turnover_df["month"] = pd.to_datetime(sales_turnover_df["month"])
        sales_turnover_df = sales_turnover_df[sales_turnover_df["month"] >= cutoff_date]

        if len(sales_turnover_df) == 0:
            return None

        total_days = self.history_days
        rows: list[dict] = []

        for nomenclature_ref, group in sales_turnover_df.groupby("nomenclature_ref"):
            total_qty = group["quantity"].sum()
            avg_daily = total_qty / total_days if total_days > 0 else 0.0
            first_row = group.iloc[0]
            rows.append({
                "nomenclature_ref": nomenclature_ref,
                "nomenclature_name": first_row["nomenclature_name"],
                "total_qty_history": total_qty,
                "avg_daily_qty": avg_daily,
            })

        return pd.DataFrame(rows) if rows else None

    def _build_forecast_demand(self, avg_daily_df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
        if avg_daily_df is None or len(avg_daily_df) == 0:
            return None

        rows: list[dict] = []
        for _, row in avg_daily_df.iterrows():
            nomenclature_ref = row["nomenclature_ref"]
            avg_daily = row["avg_daily_qty"]
            for horizon in self.horizons_days:
                forecast_qty = avg_daily * horizon
                rows.append({
                    "nomenclature_ref": nomenclature_ref,
                    "nomenclature_name": row["nomenclature_name"],
                    "horizon_days": horizon,
                    "avg_daily_qty": avg_daily,
                    "forecast_qty": forecast_qty,
                })
        return pd.DataFrame(rows) if rows else None

    def _build_material_requirement(
        self,
        forecast_df: Optional[pd.DataFrame],
        bom_flat_df: pd.DataFrame,
        nomenclature_lookup: dict
    ) -> Optional[pd.DataFrame]:
        if forecast_df is None or len(forecast_df) == 0:
            return None

        rows: list[dict] = []
        forecast_filtered = forecast_df[forecast_df["horizon_days"]
                                        == self.default_horizon]

        for _, forecast_row in forecast_filtered.iterrows():
            product_ref = forecast_row["nomenclature_ref"]
            forecast_qty = forecast_row["forecast_qty"]
            bom_components = bom_flat_df[bom_flat_df["product_ref"]
                                         == product_ref]

            for _, bom_row in bom_components.iterrows():
                component_ref = bom_row["component_ref"]
                quantity_per_unit = bom_row["quantity_per_unit"]
                is_operation = bom_row.get("is_operation", False)
                material_need = forecast_qty * quantity_per_unit
                component_info = nomenclature_lookup.get(component_ref, {})

                rows.append({
                    "product_ref": product_ref,
                    "product_name": forecast_row["nomenclature_name"],
                    "horizon_days": self.default_horizon,
                    "forecast_qty": forecast_qty,
                    "component_ref": component_ref,
                    "component_name": bom_row["component_name"],
                    "quantity_per_unit": quantity_per_unit,
                    "material_need": material_need,
                    "is_operation": is_operation,
                })

        return pd.DataFrame(rows) if rows else None

    def _build_purchase_recommendation(
        self,
        material_req_df: Optional[pd.DataFrame],
        stock_balance_df: pd.DataFrame,
        prices_lookup: dict,
        nomenclature_lookup: dict
    ) -> Optional[pd.DataFrame]:
        if material_req_df is None or len(material_req_df) == 0:
            return None

        material_req_df = material_req_df[material_req_df["is_operation"] == False]
        rows: list[dict] = []

        for _, req_row in material_req_df.iterrows():
            component_ref = req_row["component_ref"]
            material_need = req_row["material_need"]
            stock_row = stock_balance_df[stock_balance_df["Номенклатура_Key"]
                                         == component_ref]
            on_hand = stock_row["Количество"].sum() if len(
                stock_row) > 0 else 0.0
            shortage = material_need - on_hand

            if shortage > 0:
                purchase_price = prices_lookup.get(component_ref, 0.0)
                purchase_sum = shortage * purchase_price
                rows.append({
                    "component_ref": component_ref,
                    "component_name": req_row["component_name"],
                    "material_need": material_need,
                    "on_hand": on_hand,
                    "shortage": shortage,
                    "purchase_price": purchase_price,
                    "purchase_sum": purchase_sum,
                })

        return pd.DataFrame(rows) if rows else None

    def _build_stock_coverage(
        self,
        stock_balance_df: pd.DataFrame,
        avg_daily_df: Optional[pd.DataFrame],
        nomenclature_lookup: dict
    ) -> Optional[pd.DataFrame]:
        if stock_balance_df is None or avg_daily_df is None:
            return None

        rows: list[dict] = []
        stock_grouped = stock_balance_df.groupby("Номенклатура_Key")[
            "Количество"].sum().reset_index()

        for _, stock_row in stock_grouped.iterrows():
            nomenclature_ref = stock_row["Номенклатура_Key"]
            on_hand = stock_row["Количество"]
            avg_row = avg_daily_df[avg_daily_df["nomenclature_ref"]
                                   == nomenclature_ref]

            if len(avg_row) > 0:
                avg_daily = avg_row.iloc[0]["avg_daily_qty"]
                coverage_days = on_hand / avg_daily if avg_daily > 0 else None
            else:
                avg_daily = 0.0
                coverage_days = None

            nomenclature_info = nomenclature_lookup.get(nomenclature_ref, {})
            rows.append({
                "nomenclature_ref": nomenclature_ref,
                "nomenclature_name": nomenclature_info.get("Description"),
                "on_hand": on_hand,
                "avg_daily_qty": avg_daily,
                "coverage_days": coverage_days,
            })

        return pd.DataFrame(rows) if rows else None

    def _build_production_cost_estimate(
        self,
        bom_flat_df: pd.DataFrame,
        nomenclature_lookup: dict
    ) -> Optional[pd.DataFrame]:
        rows: list[dict] = []
        for product_ref, group in bom_flat_df.groupby("product_ref"):
            total_cost = group["cost"].sum()
            product_info = nomenclature_lookup.get(product_ref, {})
            rows.append({
                "product_ref": product_ref,
                "product_name": product_info.get("Description"),
                "estimated_cost": total_cost,
            })
        return pd.DataFrame(rows) if rows else None

    def _build_profitability_analysis(
        self,
        sales_doc_lines_df: pd.DataFrame,
        purchase_doc_lines_df: pd.DataFrame,
        nomenclature_lookup: dict
    ) -> Optional[pd.DataFrame]:
        rows: list[dict] = []

        sales_grouped = sales_doc_lines_df.groupby("Номенклатура_Key").agg({
            "Сумма": "sum",
            "Количество": "sum"
        }).reset_index()
        sales_grouped["avg_sale_price"] = sales_grouped["Сумма"] / \
            sales_grouped["Количество"]

        purchase_grouped = purchase_doc_lines_df.groupby("Номенклатура_Key").agg({
            "Сумма": "sum",
            "Количество": "sum"
        }).reset_index()
        purchase_grouped["avg_purchase_price"] = purchase_grouped["Сумма"] / \
            purchase_grouped["Количество"]

        merged = sales_grouped.merge(
            purchase_grouped, on="Номенклатура_Key", how="inner")

        for _, row in merged.iterrows():
            nomenclature_ref = row["Номенклатура_Key"]
            avg_sale_price = row["avg_sale_price"]
            avg_purchase_price = row["avg_purchase_price"]
            margin = avg_sale_price - avg_purchase_price
            margin_pct = (margin / avg_purchase_price *
                          100) if avg_purchase_price > 0 else 0.0
            nomenclature_info = nomenclature_lookup.get(nomenclature_ref, {})

            rows.append({
                "nomenclature_ref": nomenclature_ref,
                "nomenclature_name": nomenclature_info.get("Description"),
                "avg_sale_price": avg_sale_price,
                "avg_purchase_price": avg_purchase_price,
                "margin": margin,
                "margin_pct": margin_pct,
            })

        return pd.DataFrame(rows) if rows else None


forecast_builder = ForecastBuilder()
