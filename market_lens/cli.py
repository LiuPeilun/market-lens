from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Annotated

import typer

from market_lens.agent.market_agent import MarketAnalysisAgent
from market_lens.backtesting.engine import run_backtest
from market_lens.backtesting.io import load_backtest_dataset
from market_lens.backtesting.models import BacktestConfiguration, BacktestDataError

app = typer.Typer(help="Market Lens command line tools.")


@app.callback()
def main() -> None:
    pass


@app.command()
def analyze(
    asset_type: Annotated[str, typer.Argument(help="stock or fund")],
    code: Annotated[str, typer.Argument(help="Stock symbol or fund code")],
    start: Annotated[str, typer.Option("--start", help="Start date, for example 2018-01-01")],
    end: Annotated[str | None, typer.Option("--end", help="End date")] = None,
) -> None:
    agent = MarketAnalysisAgent()
    result = agent.analyze(
        asset_type=asset_type,
        code=code,
        start=parse_cli_date(start),
        end=parse_cli_date(end) if end else date.today(),
    )
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2, default=str))


@app.command("backtest")
def backtest(
    dataset: Annotated[
        Path,
        typer.Argument(help="Point-in-time assessment and price dataset in JSON format"),
    ],
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Write the JSON report to this path"),
    ] = None,
    bucket_count: Annotated[
        int,
        typer.Option("--buckets", min=2, help="Cross-sectional score bucket count"),
    ] = 5,
    minimum_cross_section: Annotated[
        int,
        typer.Option("--minimum-cross-section", min=2),
    ] = 10,
) -> None:
    try:
        snapshots, prices, benchmark = load_backtest_dataset(dataset)
        report = run_backtest(
            snapshots,
            prices,
            benchmark_prices=benchmark,
            configuration=BacktestConfiguration(
                bucket_count=bucket_count,
                minimum_cross_section=minimum_cross_section,
            ),
        )
    except (BacktestDataError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if output is None:
        typer.echo(rendered)
        return
    output.write_text(rendered + "\n", encoding="utf-8")
    typer.echo(f"Backtest report written to {output}")


def parse_cli_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise typer.BadParameter("Expected date format YYYY-MM-DD") from exc


if __name__ == "__main__":
    app()
