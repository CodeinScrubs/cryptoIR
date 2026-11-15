"""Explicit fiat units, finite conversion budgets and matched paper benchmarks."""

import hashlib
import json
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_DOWN

from .domain import Book, DataError, number, walk
from .feeds import public_response
from .stream_audit import book_from_dict
from .streams import dumps

ZERO = Decimal(0)
ALL_BOOKS = "https://apiv2.nobitex.ir/v3/orderbook/all"
FIAT_UNIT_SOURCE = "https://github.com/nobitex/docs-api/blob/master/source/includes/_websocket.md"


@dataclass(frozen=True)
class ConversionRules:
    quantity_step: Decimal
    minimum_notional: Decimal
    fee_rate: Decimal
    participation: Decimal

    def __post_init__(self):
        for key in asdict(self):
            object.__setattr__(self,key,number(getattr(self,key)))
        if self.quantity_step<=0 or self.minimum_notional<=0 or not 0<=self.fee_rate<1 or not 0<self.participation<=1:
            raise DataError("explicit conversion step, minimum, fee and participation required")


def snapshot_pair(record: dict, pair: str) -> Book:
    from .feeds import native_symbol, normalize, endpoint
    if record.get("kind")!="snapshot" or record.get("url")!=ALL_BOOKS or record["payload"].get("status")!="ok":
        raise DataError("not a successful all-market source response")
    if hashlib.sha256(record["raw_body"].encode()).hexdigest()!=record["body_sha256"]:
        raise DataError("all-market source hash mismatch")
    if dumps(json.loads(record["raw_body"],parse_float=Decimal))!=dumps(record["payload"]):
        raise DataError("all-market payload differs from raw body")
    symbol=native_symbol("nobitex",pair)
    payload=record["payload"][symbol]
    # Keep raw prices as IRR for the explicitly supported native IRT markets.
    return normalize({**record,"venue":"nobitex","pair":pair,"url":endpoint("nobitex",pair),
                      "payload":{"status":"ok",**payload}})


def buy_budget(book: Book, budget, rules: ConversionRules) -> dict:
    """Take only the configured displayed-depth fraction. Buy fee is received base."""
    budget=number(budget)
    if budget<=0:
        raise DataError("conversion budget must be positive")
    remaining,gross=budget,ZERO
    for level in book.asks:
        take=min(level.quantity*rules.participation,remaining/level.price)
        gross+=take
        remaining-=take*level.price
        if remaining<=0:
            break
    gross=(gross/rules.quantity_step).to_integral_value(rounding=ROUND_DOWN)*rules.quantity_step
    # Walk the SAME fraction used for sizing, not the full unadjusted book.
    from .domain import Level
    quote=walk(tuple(Level(l.price,l.quantity*rules.participation) for l in book.asks),gross) if gross else None
    if quote is None or quote.filled!=gross or quote.notional<rules.minimum_notional:
        raise DataError("capital or displayed conversion liquidity is below the minimum")
    if quote.notional>budget:
        raise AssertionError("conversion exceeded its finite budget")
    fee=gross*rules.fee_rate
    return {"gross_base":gross,"net_base":gross-fee,"fee_base":fee,"spent_quote":quote.notional,
            "residual_quote":budget-quote.notional,"complete_budget_deployment":budget-quote.notional<book.asks[0].price*rules.quantity_step,
            "execution_model":"conditional_displayed_snapshot","execution_verified":False}


def sell_value(book: Book, quantity, rules: ConversionRules) -> dict:
    from .domain import Level
    quantity=number(quantity)
    if quantity<0:
        raise DataError("negative holdings cannot be valued as spot")
    sellable=(quantity/rules.quantity_step).to_integral_value(rounding=ROUND_DOWN)*rules.quantity_step
    if not sellable or sellable*book.bids[0].price<rules.minimum_notional:
        return {"quantity":quantity,"sellable":ZERO,"dust":quantity,"net_quote":ZERO,"fee_quote":ZERO}
    quote=walk(tuple(Level(l.price,l.quantity*rules.participation) for l in book.bids),sellable)
    if quote.filled!=sellable:
        raise DataError("insufficient displayed exit depth; valuation unavailable")
    if quote.notional<rules.minimum_notional:
        return {"quantity":quantity,"sellable":ZERO,"dust":quantity,"net_quote":ZERO,"fee_quote":ZERO}
    fee=quote.notional*rules.fee_rate
    return {"quantity":quantity,"sellable":sellable,"dust":quantity-sellable,"net_quote":quote.notional-fee,"fee_quote":fee}


def validate_books(fx: Book, spot: Book, now_ns: int, max_age_ns: int):
    if fx.venue!="nobitex" or fx.pair!="USDT/IRR" or spot.venue!=fx.venue or spot.pair!="BTC/USDT":
        raise DataError("initial matched benchmark supports Nobitex BTC/USDT and USDT/IRR only")
    for book in (fx,spot):
        book.validate_age(now_ns,max_age_ns)
        if book.exchange_ns is None:
            raise DataError("conversion valuation requires source times")


def create_capital_receipt(fx: Book, spot: Book, initial_irt, fx_rules: ConversionRules,
                           spot_rules: ConversionRules, *, now_ns: int, max_age_ns: int, source_sha256: str) -> dict:
    validate_books(fx,spot,now_ns,max_age_ns)
    initial_irt=number(initial_irt)
    if initial_irt<=0:
        raise DataError("positive IRT capital required")
    initial_irr=initial_irt*10
    conversion=buy_budget(fx,initial_irr,fx_rules)
    btc=buy_budget(spot,conversion["net_base"],spot_rules)
    mid=(fx.bids[0].price+fx.asks[0].price)/2
    receipt={"schema":1,"kind":"paper_irt_capital_receipt","created_ns":now_ns,"initial_irt":initial_irt,
             "initial_irr":initial_irr,"initial_usdt_numeraire":initial_irr/mid,"initial_mid_irr_per_usdt":mid,
             "venue":"nobitex","pair":"BTC/USDT","native_fx_symbol":"USDTIRT","fx_price_unit":"IRR",
             "unit_source":FIAT_UNIT_SOURCE,"source_sha256":source_sha256,"fx_book":asdict(fx),"spot_book":asdict(spot),
             "fx_rules":asdict(fx_rules),"spot_rules":asdict(spot_rules),"conversion":conversion,
             "benchmarks":{"usdt_hold":{"usdt":conversion["net_base"],"btc":ZERO},
                           "btc_hold":{"usdt":btc["residual_quote"],"btc":btc["net_base"],"purchase":btc},
                           "irt_cash":{"irr":initial_irr}},
             "fee_schedule_account_verified":False,"fill_model_calibrated":False,"live_execution":False}
    receipt["receipt_sha256"]=hashlib.sha256(dumps(receipt).encode()).hexdigest()
    return receipt


def verify_receipt(receipt: dict):
    if receipt.get("schema")!=1 or receipt.get("kind")!="paper_irt_capital_receipt" or receipt.get("live_execution") is not False:
        raise DataError("unsupported paper capital receipt")
    expected=dict(receipt)
    digest=expected.pop("receipt_sha256")
    if hashlib.sha256(dumps(expected).encode()).hexdigest()!=digest:
        raise DataError("capital receipt hash mismatch")
    rebuilt=create_capital_receipt(book_from_dict(receipt["fx_book"]),book_from_dict(receipt["spot_book"]),
        number(receipt["initial_irt"]),ConversionRules(**receipt["fx_rules"]),ConversionRules(**receipt["spot_rules"]),
        now_ns=receipt["created_ns"],max_age_ns=5_000_000_000,source_sha256=receipt["source_sha256"])
    if dumps(rebuilt)!=dumps(receipt):
        raise DataError("capital receipt differs from deterministic reconstruction")


def matched_valuation(receipt: dict, fx: Book, spot: Book, usdt, btc, *, now_ns: int, max_age_ns: int) -> dict:
    verify_receipt(receipt)
    validate_books(fx,spot,now_ns,max_age_ns)
    if now_ns<receipt["created_ns"]:
        raise DataError("valuation precedes capital receipt")
    fx_rules,spot_rules=ConversionRules(**receipt["fx_rules"]),ConversionRules(**receipt["spot_rules"])
    residual_irr=number(receipt["conversion"]["residual_quote"])
    current_mid=(fx.bids[0].price+fx.asks[0].price)/2
    initial_irt,initial_usdt=number(receipt["initial_irt"]),number(receipt["initial_usdt_numeraire"])

    def portfolio(quote,base):
        quote,base=number(quote),number(base)
        if min(quote,base)<0:
            raise DataError("negative paper balance")
        base_exit=sell_value(spot,base,spot_rules)
        quote_total=quote+base_exit["net_quote"]
        fiat_exit=sell_value(fx,quote_total,fx_rules)
        irr=residual_irr+fiat_exit["net_quote"]
        return {"irt_liquidation_mark":irr/10,"usdt_numeraire_mark":irr/current_mid,
                "net_return_irt":irr/(initial_irt*10)-1,"net_return_usdt_numeraire":irr/current_mid/initial_usdt-1,
                "spot_liquidation_usdt":quote_total,"spot_exit":base_exit,"fiat_exit":fiat_exit,
                "residual_irr":residual_irr}

    current=portfolio(usdt,btc)
    benchmarks={name:portfolio(value["usdt"],value["btc"]) for name,value in receipt["benchmarks"].items() if name!="irt_cash"}
    benchmarks["irt_cash"]={"irt_liquidation_mark":initial_irt,"usdt_numeraire_mark":initial_irt*10/current_mid,
                            "net_return_irt":ZERO,"net_return_usdt_numeraire":initial_irt*10/current_mid/initial_usdt-1}
    fx_factor=current_mid/number(receipt["initial_mid_irr_per_usdt"])
    neutral_factor=1+current["net_return_usdt_numeraire"]
    return {"schema":1,"kind":"matched_paper_valuation","observed_ns":now_ns,"capital_receipt_sha256":receipt["receipt_sha256"],
            "portfolio":current,"benchmarks":benchmarks,"current_mid_irr_per_usdt":current_mid,
            "fx_return_mid":fx_factor-1,"attribution_identity_error":(1+current["net_return_irt"])-fx_factor*neutral_factor,
            "excess_return_irt_vs_usdt_hold":current["net_return_irt"]-benchmarks["usdt_hold"]["net_return_irt"],
            "excess_return_irt_vs_btc_hold":current["net_return_irt"]-benchmarks["btc_hold"]["net_return_irt"],
            "fills_verified":False,"profitability_established":False,"live_execution":False,
            "limits":"conditional independent benchmark depth; midpoint used only as numeraire; no deposits/withdrawals or external operating costs"}
