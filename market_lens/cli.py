from __future__ import annotations

import json
from datetime import date, datetime
from typing import Annotated

import typer

from market_lens.agent.market_agent import MarketAnalysisAgent

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


def parse_cli_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise typer.BadParameter("Expected date format YYYY-MM-DD") from exc


if __name__ == "__main__":
    app()
