"""Prepare a finite IRT paper account from recorded public metadata and books."""

import hashlib
from dataclasses import asdict
from pathlib import Path
import time

from .catalog import fetch_catalog
from .domain import DataError, number
from .feeds import public_response
from .forward import ForwardConfig
from .runtime import atomic_json
from .streams import dumps
from .valuation import ALL_BOOKS, ConversionRules, create_capital_receipt, snapshot_pair


def fund_paper(directory: Path, initial_irt, template: ForwardConfig, fx_fee_rate) -> dict:
    if template.pair!="BTC/USDT" or template.capital_basis!="synthetic_reference_usdt":
        raise DataError("funding template must be a synthetic-reference BTC/USDT paper config")
    initial_irt,fx_fee_rate=number(initial_irt),number(fx_fee_rate)
    if initial_irt<=0 or not 0<=fx_fee_rate<1:
        raise DataError("explicit positive IRT capital and valid assumed fiat fee required")
    directory.mkdir(parents=True,exist_ok=False)
    metadata=fetch_catalog("nobitex")
    (directory/"funding-metadata.json").write_text(dumps(metadata),encoding="utf-8")
    if metadata["kind"]!="snapshot":
        raise DataError("public market metadata unavailable; no capital allocated")
    market={m["native_symbol"]:m for m in metadata["markets"]}
    for symbol in ("BTCUSDT","USDTIRT"):
        if symbol not in market or number(market[symbol]["native_scale"])!=1 or not market[symbol]["base_currency_active"]:
            raise DataError("required public asset metadata unavailable")
    minimum_irr=number(metadata["payload"]["nobitex"]["minOrders"]["rls"])
    fx_rules=ConversionRules(market["USDTIRT"]["quantity_step"],minimum_irr,fx_fee_rate,template.risk.depth_participation)
    spot_rules=ConversionRules(market["BTCUSDT"]["quantity_step"],market["BTCUSDT"]["minimum_notional"],
                               template.rules.fee_rate,template.risk.depth_participation)
    record=public_response(ALL_BOOKS)
    encoded=dumps(record).encode()
    (directory/"funding-books.json").write_bytes(encoded)
    receipt=create_capital_receipt(snapshot_pair(record,"USDT/IRR"),snapshot_pair(record,"BTC/USDT"),initial_irt,
                                   fx_rules,spot_rules,now_ns=time.time_ns(),max_age_ns=5_000_000_000,
                                   source_sha256=hashlib.sha256(encoded).hexdigest())
    atomic_json(directory/"capital-receipt.json",receipt)
    value=asdict(template)
    value.update(capital_basis="paper_irt_conversion",capital_receipt_sha256=receipt["receipt_sha256"])
    capital=receipt["conversion"]["net_base"]
    cap=min(template.risk.maximum_order_notional,capital*template.risk.max_exposure_fraction)
    value["risk"].update(initial_quote=capital,maximum_order_notional=cap)
    value["rules"].update(quantity_step=spot_rules.quantity_step,price_tick=number(market["BTCUSDT"]["price_tick"]),
                           minimum_notional=spot_rules.minimum_notional,maximum_notional=cap)
    config=ForwardConfig.from_dict(value)
    if cap<config.rules.minimum_notional:
        raise DataError("capital/risk allocation cannot meet minimum order size")
    atomic_json(directory/"config.json",asdict(config))
    return {"kind":"paper_funding_prepared","initial_irt":initial_irt,"paper_usdt":capital,
            "residual_irr":receipt["conversion"]["residual_quote"],"maximum_order_notional_usdt":cap,
            "observed_minimum_notional_usdt":config.rules.minimum_notional,
            "capital_receipt_sha256":receipt["receipt_sha256"],"live_execution":False,
            "account_fees_verified":False,"depth_fills_verified":False}
