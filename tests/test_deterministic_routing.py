from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from market_lens.types import ReitProfile, StockProfile
from market_lens.valuation.routing import (
    CommodityEquityMapping,
    StyleRoutingEvidence,
    route_asset_model,
)

AS_OF = date(2026, 7, 22)


def stock_profile(
    em_industry: str | None,
    csrc_industry: str | None = None,
    *,
    name: str = "测试公司",
) -> StockProfile:
    return StockProfile(
        code="600000",
        name=name,
        em_industry=em_industry,
        csrc_industry=csrc_industry,
        security_type="A股",
        raw={},
    )


def reit_profile(*, fund_type: str = "Reits") -> ReitProfile:
    return ReitProfile(
        fund_code="180101",
        fund_name="博时蛇口产园REIT",
        full_name="博时招商蛇口产业园封闭式基础设施证券投资基金",
        fund_type=fund_type,
        establishment_date=date(2021, 6, 7),
        term_text="50年",
        scale_report_date=date(2025, 12, 31),
        period_end_net_assets_cny=3017800392.7,
        exchange="SZSE",
        quote_id="0.180101",
        source="eastmoney_fund_mobile",
        raw={},
    )


def commodity_mapping(exposure: str) -> CommodityEquityMapping:
    return CommodityEquityMapping(
        exposure=exposure,  # type: ignore[arg-type]
        method="structured_business_segment",
        source="audited_annual_report_business_segments",
        as_of=AS_OF,
    )


def style_evidence(
    *,
    dividend: bool = False,
    growth: bool = False,
    source: str = "point_in_time_factor_classifier",
) -> StyleRoutingEvidence:
    return StyleRoutingEvidence(
        source=source,
        as_of=AS_OF,
        ruleset_version="style-candidate-v1",
        dividend_quality_eligible=dividend,
        growth_quality_eligible=growth,
    )


def test_exact_reit_profile_has_priority_over_declared_type_and_styles() -> None:
    route = route_asset_model(
        declared_asset_type="stock",
        stock_profile=stock_profile("银行"),
        financial_scope="bank",
        reit_profile=reit_profile(),
        style_evidence=style_evidence(dividend=True, growth=True),
    )

    assert route.asset_type == "reit"
    assert route.main_model == "reit_basic"
    assert route.style_overlays == ()
    assert route.reason == "eastmoney_exact_reit_type"
    assert "declared_asset_type_conflict:stock!=reit" in route.warnings
    assert "stock_style_overlays_not_applicable:reit" in route.warnings
    assert route.scoring_eligible is False


def test_reit_profile_rejects_non_reit_type() -> None:
    with pytest.raises(ValueError, match="FTYPE=Reits"):
        route_asset_model(
            declared_asset_type="fund",
            reit_profile=reit_profile(fund_type="混合型-偏股"),
        )


def test_reit_profile_rejects_mismatched_exchange_route() -> None:
    invalid = replace(reit_profile(), quote_id="1.180101")

    with pytest.raises(ValueError, match="exchange quote route"):
        route_asset_model(declared_asset_type="fund", reit_profile=invalid)


@pytest.mark.parametrize(
    "profile",
    ["etf", "etf_linked", "index_fund", "active_fund"],
)
def test_fund_route_reuses_existing_product_classification(profile: str) -> None:
    route = route_asset_model(
        declared_asset_type="fund",
        fund_product_profile=profile,  # type: ignore[arg-type]
    )

    assert route.asset_type == "fund"
    assert route.main_model == profile
    assert route.reason == "existing_fund_product_classification"
    assert route.scoring_eligible is False


def test_unclassified_fund_fails_closed_and_suppresses_stock_styles() -> None:
    route = route_asset_model(
        declared_asset_type="fund",
        style_evidence=style_evidence(dividend=True),
    )

    assert route.main_model == "fund_unclassified"
    assert route.style_overlays == ()
    assert "fund_product_profile_unavailable" in route.warnings
    assert "stock_style_overlays_not_applicable:fund" in route.warnings


@pytest.mark.parametrize(
    ("industry", "expected_model"),
    [
        ("房地产开发", "real_estate_developer"),
        ("钢铁行业", "cyclical_steel"),
        ("电力行业", "regulated_utility_basic"),
        ("半导体", "technology_rd"),
        ("医疗器械", "medical_device"),
        ("化学制药", "mature_pharma"),
    ],
)
def test_stock_industry_routes_are_deterministic(
    industry: str,
    expected_model: str,
) -> None:
    route = route_asset_model(
        declared_asset_type="stock",
        stock_profile=stock_profile(industry),
        financial_scope="general_non_financial",
    )

    assert route.asset_type == "stock"
    assert route.main_model == expected_model
    assert route.reason == "industry_classification"
    assert route.style_overlays == ()
    assert route.scoring_eligible is False


def test_financial_org_type_wins_without_being_replaced_by_styles() -> None:
    route = route_asset_model(
        declared_asset_type="stock",
        stock_profile=stock_profile("保险"),
        financial_scope="bank",
        style_evidence=style_evidence(dividend=True, growth=True),
    )

    assert route.main_model == "bank"
    assert route.style_overlays == (
        "dividend_quality_overlay",
        "growth_quality_overlay",
    )
    assert route.reason == "financial_org_type"
    assert "financial_industry_classification_conflict:bank!=insurance" in route.warnings
    assert "style_ruleset:style-candidate-v1" in route.evidence


def test_industry_financial_model_is_only_a_fallback() -> None:
    route = route_asset_model(
        declared_asset_type="stock",
        stock_profile=stock_profile("银行"),
        financial_scope="unknown",
    )

    assert route.main_model == "bank"
    assert route.reason == "industry_classification"
    assert "financial_org_type_unavailable" in route.warnings


def test_conflicting_industry_sources_fail_closed_to_generic() -> None:
    route = route_asset_model(
        declared_asset_type="stock",
        stock_profile=stock_profile("房地产开发", "软件和信息技术服务业"),
        financial_scope="general_non_financial",
    )

    assert route.main_model == "generic_non_financial"
    assert route.reason == "generic_fallback"
    assert any(
        warning.startswith("industry_classification_conflict:")
        for warning in route.warnings
    )


@pytest.mark.parametrize(
    ("industry", "warning"),
    [
        ("物业管理", "em_industry:real_estate_service_excluded"),
        ("创新药", "em_industry:innovative_pharma_model_unavailable"),
        ("电力设备", None),
    ],
)
def test_unsupported_or_adjacent_industries_do_not_false_route(
    industry: str,
    warning: str | None,
) -> None:
    route = route_asset_model(
        declared_asset_type="stock",
        stock_profile=stock_profile(industry),
        financial_scope="general_non_financial",
    )

    assert route.main_model == "generic_non_financial"
    if warning:
        assert warning in route.warnings


def test_nonferrous_requires_explicit_supported_commodity_mapping() -> None:
    enhanced = route_asset_model(
        declared_asset_type="stock",
        stock_profile=stock_profile("工业金属"),
        financial_scope="general_non_financial",
        commodity_mapping=commodity_mapping("copper"),
    )
    missing = route_asset_model(
        declared_asset_type="stock",
        stock_profile=stock_profile("工业金属"),
        financial_scope="general_non_financial",
    )
    mismatched = route_asset_model(
        declared_asset_type="stock",
        stock_profile=stock_profile("工业金属"),
        financial_scope="general_non_financial",
        commodity_mapping=commodity_mapping("coking_coal"),
    )

    assert enhanced.main_model == "cyclical_nonferrous"
    assert enhanced.reason == "industry_and_explicit_commodity_mapping"
    assert "commodity_exposure:copper" in enhanced.evidence
    assert missing.main_model == "cyclical_nonferrous_financial"
    assert mismatched.main_model == "cyclical_nonferrous_financial"
    assert "nonferrous_commodity_mapping_mismatch:coking_coal" in mismatched.warnings


def test_coal_routes_coking_mapping_but_thermal_coal_to_financial_only() -> None:
    coking = route_asset_model(
        declared_asset_type="stock",
        stock_profile=stock_profile("煤炭开采和洗选业"),
        financial_scope="general_non_financial",
        commodity_mapping=commodity_mapping("coking_coal"),
    )
    thermal = route_asset_model(
        declared_asset_type="stock",
        stock_profile=stock_profile("煤炭行业"),
        financial_scope="general_non_financial",
        commodity_mapping=commodity_mapping("thermal_coal"),
    )

    assert coking.main_model == "cyclical_coking_coal"
    assert thermal.main_model == "cyclical_coal_financial"
    assert "thermal_coal_current_price_history_unavailable" in thermal.warnings


def test_company_name_and_mapping_cannot_create_an_industry_route() -> None:
    route = route_asset_model(
        declared_asset_type="stock",
        stock_profile=stock_profile("制造业", name="某铜业股份有限公司"),
        financial_scope="general_non_financial",
        commodity_mapping=commodity_mapping("copper"),
    )

    assert route.main_model == "generic_non_financial"
    assert (
        "commodity_mapping_ignored_without_supported_industry:copper"
        in route.warnings
    )
    assert not any(item.startswith("commodity_exposure:") for item in route.evidence)


def test_style_overlay_requires_complete_provenance() -> None:
    route = route_asset_model(
        declared_asset_type="stock",
        stock_profile=stock_profile("电力行业"),
        financial_scope="general_non_financial",
        style_evidence=style_evidence(dividend=True, source=""),
    )

    assert route.main_model == "regulated_utility_basic"
    assert route.style_overlays == ()
    assert "style_evidence_provenance_incomplete" in route.warnings


def test_invalid_commodity_mapping_provenance_is_rejected() -> None:
    with pytest.raises(ValueError, match="source"):
        CommodityEquityMapping(
            exposure="copper",
            method="structured_business_segment",
            source="",
            as_of=AS_OF,
        )
