from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

from market_lens.agent.market_agent import MarketAnalysisAgent
from market_lens.data.eastmoney import EastmoneyError
from market_lens.types import (
    FundProductInfo,
    ReitDistribution,
    ReitFinancialSnapshot,
    ReitPeriodicReportNotice,
    ReitPriceBar,
    ReitProfile,
    StockBalanceSheet,
    StockProfile,
)
from market_lens.valuation.research_context import (
    build_fund_research_context,
    build_reit_research_context,
    build_stock_research_context,
)

ANALYSIS_AS_OF = date(2026, 3, 31)
RETRIEVED_AT = datetime(2026, 4, 1, tzinfo=UTC)


def stock_balance_sheet(*, notice_date: date | None) -> StockBalanceSheet:
    return StockBalanceSheet(
        code="600000",
        report_date=date(2025, 12, 31),
        report_type="annual",
        report_name="2025 annual report",
        notice_date=notice_date,
        source_updated_at=notice_date,
        currency="CNY",
        org_type="通用",
        raw={"must_not_escape": True},
        total_assets_cny=100.0,
        total_current_assets_cny=50.0,
        monetary_funds_cny=10.0,
        accounts_receivable_cny=None,
        inventory_cny=None,
        contract_asset_cny=None,
        total_liabilities_cny=40.0,
        total_current_liabilities_cny=20.0,
        accounts_payable_cny=None,
        contract_liability_cny=None,
        short_term_borrowings_cny=None,
        current_portion_noncurrent_liabilities_cny=None,
        long_term_borrowings_cny=None,
        bonds_payable_cny=None,
        total_equity_cny=60.0,
    )


def reit_profile() -> ReitProfile:
    return ReitProfile(
        fund_code="180101",
        fund_name="测试REIT",
        full_name="测试基础设施证券投资基金",
        fund_type="Reits",
        establishment_date=date(2021, 6, 1),
        term_text="40年",
        scale_report_date=date(2025, 12, 31),
        period_end_net_assets_cny=3_000_000_000.0,
        exchange="SZSE",
        quote_id="0.180101",
        source="eastmoney_fund_mobile",
        raw={"must_not_escape": True},
    )


def reit_price(day: date, close: float) -> ReitPriceBar:
    return ReitPriceBar(
        fund_code="180101",
        fund_name="测试REIT",
        exchange="SZSE",
        quote_id="0.180101",
        period="daily",
        date=day,
        open=close,
        close=close,
        high=close,
        low=close,
        volume=100.0,
        amount_cny=close * 100,
        amplitude_pct=0.0,
        change_pct=0.0,
        change_amount=0.0,
        turnover_pct=None,
        is_complete=True,
        source="eastmoney_push2his",
    )


def reit_financial(*, notice_date: date | None) -> ReitFinancialSnapshot:
    return ReitFinancialSnapshot(
        fund_code="180101",
        report_date=date(2025, 12, 31),
        report_kind="annual",
        notice_date=notice_date,
        realized_income_cny=10.0,
        net_profit_cny=12.0,
        unit_profit_cny=0.1,
        net_asset_growth_pct=1.0,
        fund_net_asset_growth_pct=1.0,
        distributable_profit_cny=None,
        distributable_profit_per_unit_cny=None,
        period_end_net_assets_cny=3_000_000_000.0,
        period_end_unit_nav_cny=2.0,
        fund_share_nav_growth_pct=1.0,
        point_in_time_eligible=notice_date is not None,
        source="eastmoney_fund_financial",
        raw={"must_not_escape": True},
    )


def test_stock_research_filters_statements_by_notice_date() -> None:
    context = build_stock_research_context(
        analysis_as_of=ANALYSIS_AS_OF,
        stock_profile=StockProfile(
            code="600000",
            name="测试公司",
            em_industry="软件开发",
            csrc_industry=None,
            security_type="A股",
            raw={},
        ),
        financial_scope="general_non_financial",
        balance_sheets=[
            stock_balance_sheet(notice_date=date(2026, 3, 20)),
            stock_balance_sheet(notice_date=None),
            stock_balance_sheet(notice_date=date(2026, 4, 2)),
        ],
        income_statements=[],
        cash_flow_statements=[],
        errors={},
        retrieved_at=RETRIEVED_AT,
    )

    dataset = context["datasets"]["detailed_financials"]["balance_sheet"]
    assert context["scoring_eligible"] is False
    assert context["route"]["scoring_eligible"] is False
    assert dataset["status"] == "partial"
    assert dataset["coverage"] == 0.3333
    assert dataset["unit"] == "CNY unless field suffix is _pct"
    assert dataset["excluded"] == {
        "missing_notice_date": 1,
        "notice_after_analysis_as_of": 1,
    }
    assert len(dataset["items"]) == 1
    assert "raw" not in dataset["items"][0]


def test_financial_specialist_does_not_request_generic_statements() -> None:
    context = build_stock_research_context(
        analysis_as_of=ANALYSIS_AS_OF,
        stock_profile=StockProfile(
            code="600000",
            name="测试银行",
            em_industry="银行",
            csrc_industry="货币金融服务",
            security_type="A股",
            raw={},
        ),
        financial_scope="bank",
        balance_sheets=[],
        income_statements=[],
        cash_flow_statements=[],
        errors={},
        retrieved_at=RETRIEVED_AT,
    )

    assert context["route"]["main_model"] == "bank"
    assert context["datasets"]["detailed_financials"]["status"] == "not_applicable"


def test_ordinary_fund_research_is_route_only() -> None:
    context = build_fund_research_context(
        analysis_as_of=ANALYSIS_AS_OF,
        product_profile="etf_linked",
        retrieved_at=RETRIEVED_AT,
    )

    assert context["route"]["asset_type"] == "fund"
    assert context["route"]["main_model"] == "etf_linked"
    assert context["datasets"] == {}
    assert context["scoring_eligible"] is False


def test_reit_research_requires_point_in_time_disclosures() -> None:
    valid_notice = ReitPeriodicReportNotice(
        fund_code="180101",
        title="2025年年度报告",
        category="3",
        publish_date=date(2026, 3, 20),
        attachment_type="pdf",
        announcement_id="annual-2025",
        attachment_url="https://example.test/annual.pdf",
        report_date=date(2025, 12, 31),
        report_kind="annual",
        is_canonical=True,
        source="eastmoney_fund_announcement",
        raw={"must_not_escape": True},
    )
    valid_distribution = ReitDistribution(
        fund_code="180101",
        year=2025,
        record_date=date(2026, 3, 25),
        ex_dividend_date=date(2026, 3, 26),
        cash_per_unit_cny=0.05,
        payment_date=date(2026, 3, 30),
        announcement_date=date(2026, 3, 20),
        available_date=date(2026, 3, 20),
        point_in_time_eligible=True,
        source="eastmoney_fund_distribution",
        raw_row=("must", "not", "escape"),
    )
    unavailable_distribution = replace(
        valid_distribution,
        announcement_date=None,
        available_date=None,
        point_in_time_eligible=False,
    )
    context = build_reit_research_context(
        analysis_as_of=ANALYSIS_AS_OF,
        profile=reit_profile(),
        prices=[reit_price(date(2026, 3, 30), 1.5)],
        financials=[
            reit_financial(notice_date=date(2026, 3, 20)),
            reit_financial(notice_date=None),
        ],
        distributions=[valid_distribution, unavailable_distribution],
        notices=[valid_notice],
        errors={},
        retrieved_at=RETRIEVED_AT,
    )

    assert context["route"]["asset_type"] == "reit"
    assert context["route"]["main_model"] == "reit_basic"
    assert context["datasets"]["financials"]["report_count"] == 1
    assert context["datasets"]["distributions"]["distribution_count"] == 1
    assert (
        context["datasets"]["financials"]["limitations"][0]
        == "DISPROFIT is not treated as AFFO"
    )
    assert context["datasets"]["reit_profile"]["available_at"] is None


class FakeReitClient:
    def get_fund_product_info(self, code: str) -> FundProductInfo:
        return FundProductInfo(
            fund_code=code,
            fund_name="测试REIT",
            fund_type="Reits",
            establishment_date=None,
            scale_report_date=None,
            period_end_net_assets_cny=None,
            management_fee_pct=None,
            custody_fee_pct=None,
            sales_service_fee_pct=None,
            benchmark=None,
            raw={},
        )

    def get_reit_profile(self, code: str) -> ReitProfile:
        return reit_profile()

    def get_reit_price_history(self, code: str, start: date, end: date):
        return [reit_price(start, 1.0), reit_price(end, 1.5)]

    def get_reit_financials(self, code: str):
        return [reit_financial(notice_date=date(2026, 3, 20))]

    def get_reit_distributions(self, code: str):
        raise EastmoneyError("distribution source unavailable")

    def get_reit_notices(self, code: str):
        return []


def test_agent_reit_path_is_research_only_and_isolates_source_failures() -> None:
    result = MarketAnalysisAgent(FakeReitClient()).analyze(  # type: ignore[arg-type]
        "fund",
        "180101",
        date(2026, 1, 1),
        ANALYSIS_AS_OF,
    )

    assert "assessment" not in result
    assert result["valuation"]["score"] is None
    assert result["valuation"]["method"] == "reit_basic_research_only"
    assert result["research"]["route"]["asset_type"] == "reit"
    assert result["research"]["datasets"]["exchange_price"]["status"] == "available"
    assert result["research"]["datasets"]["distributions"]["status"] == "error"
    assert (
        result["research"]["datasets"]["distributions"]["error"]
        == "distribution source unavailable"
    )
