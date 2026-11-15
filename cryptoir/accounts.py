"""Bounded authenticated reads. No arbitrary URL, order, transfer or key-management route."""

import base64
from dataclasses import dataclass
from decimal import Decimal
import getpass
import hashlib
import hmac
from http.client import HTTPException
import json
import os
from pathlib import Path
import re
import time
from uuid import uuid4
import warnings
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from . import __version__
from .domain import DataError, number

HOSTS = {"nobitex":"https://apiv2.nobitex.ir", "wallex":"https://api.wallex.ir",
         "tabdeal":"https://api1.tabdeal.org"}
# Exact read operations, including the documented read-only Nobitex POST.
READS = {
    "nobitex": {"profile":("GET","/users/profile",None),
                "balances":("POST","/users/wallets/list",b'{"type":"spot"}')},
    "wallex": {"fees":("GET","/v1/account/fee",None),
               "balances":("GET","/v1/account/balances",None)},
    "tabdeal": {"account":("GET","/r/api/v1/account",None)},
}
MAX_RESPONSE = 2*1024*1024


class AccountReadError(DataError):
    """Only fixed diagnostic codes; never transport exceptions or response bodies."""


def secret_text(value):
    if not isinstance(value,str) or not 1 <= len(value) <= 4096 or any(not 33 <= ord(c) <= 126 for c in value):
        raise AccountReadError("invalid_credential_format")
    return value


@dataclass(frozen=True, repr=False)
class Credentials:
    scheme: str
    key: str
    secret: str | None = None

    def __post_init__(self):
        secret_text(self.key)
        if self.secret is not None:
            secret_text(self.secret)
        if self.scheme not in ("nobitex_ed25519","nobitex_token","wallex_key","tabdeal_hmac"):
            raise AccountReadError("unknown_authentication_scheme")
        if self.scheme in ("nobitex_ed25519","tabdeal_hmac") and self.secret is None:
            raise AccountReadError("signing_secret_required")
        if self.scheme in ("nobitex_token","wallex_key") and self.secret is not None:
            raise AccountReadError("unexpected_signing_secret")

    def __repr__(self):
        return "<Credentials redacted>"


def prompt_credentials(venue: str, scheme: str | None = None) -> Credentials:
    defaults={"nobitex":"nobitex_ed25519","wallex":"wallex_key","tabdeal":"tabdeal_hmac"}
    if venue not in defaults:
        raise AccountReadError("unsupported_venue")
    scheme=scheme or defaults[venue]
    if scheme != defaults[venue] and (venue,scheme) != ("nobitex","nobitex_token"):
        raise AccountReadError("credential_venue_mismatch")
    # Fail rather than falling back to echoed input on a non-interactive terminal.
    with warnings.catch_warnings():
        warnings.simplefilter("error",getpass.GetPassWarning)
        try:
            key=getpass.getpass("API key/token (hidden; never saved): ")
            secret=(getpass.getpass("Signing secret/private key (hidden; never saved): ")
                    if scheme in ("nobitex_ed25519","tabdeal_hmac") else None)
        except (getpass.GetPassWarning, EOFError):
            raise AccountReadError("interactive_hidden_input_required") from None
    return Credentials(scheme,key,secret)


def build_read_request(venue: str, operation: str, credentials: Credentials, *, now_ns: int) -> Request:
    if venue not in READS or operation not in READS[venue]:
        raise AccountReadError("operation_not_in_read_allowlist")
    if credentials.scheme not in {"nobitex":("nobitex_ed25519","nobitex_token"),
                                  "wallex":("wallex_key",),"tabdeal":("tabdeal_hmac",)}[venue]:
        raise AccountReadError("credential_venue_mismatch")
    if type(now_ns) is not int or now_ns <= 0:
        raise AccountReadError("invalid_local_clock")
    method,path,body=READS[venue][operation]
    headers={"User-Agent":"TraderBot/cryptoIR-readonly", "Accept":"application/json", "Content-Type":"application/json"}
    if credentials.scheme == "nobitex_token":
        headers["Authorization"]="Token "+credentials.key
    elif credentials.scheme == "nobitex_ed25519":
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        stamp=str(now_ns//1_000_000_000)
        try:
            seed=base64.b64decode(credentials.secret+"="*(-len(credentials.secret)%4),altchars=b"-_",validate=True)
            private=Ed25519PrivateKey.from_private_bytes(seed)
        except (ValueError,TypeError):
            raise AccountReadError("invalid_ed25519_private_key") from None
        payload=stamp.encode()+method.encode()+path.encode()+(body or b"")
        headers.update({"Nobitex-Key":credentials.key,"Nobitex-Timestamp":stamp,
                        "Nobitex-Signature":base64.urlsafe_b64encode(private.sign(payload)).decode()})
    elif credentials.scheme == "wallex_key":
        headers["X-API-Key"]=credentials.key
    else:
        query=urlencode({"timestamp":now_ns//1_000_000})
        signature=hmac.new(credentials.secret.encode(),query.encode(),hashlib.sha256).hexdigest()
        path+="?"+query+"&signature="+signature
        headers["X-MBX-APIKEY"]=credentials.key
    return Request(HOSTS[venue]+path,data=body,headers=headers,method=method)


class NoAccountRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise AccountReadError("redirect_refused")


def _no_constant(_):
    raise AccountReadError("nonfinite_response_value")


def _unique_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:
            raise AccountReadError("duplicate_response_field")
        result[key]=value
    return result


def read_operation(venue: str, operation: str, credentials: Credentials) -> tuple[dict,dict]:
    request=build_read_request(venue,operation,credentials,now_ns=time.time_ns())
    start=time.monotonic_ns()
    # Default verified HTTPS and proxy handling; redirects cannot carry a secret elsewhere.
    try:
        with build_opener(NoAccountRedirect()).open(request,timeout=10) as response:
            if response.status != 200:
                raise AccountReadError("unexpected_http_status")
            raw=response.read(MAX_RESPONSE+1)
    except HTTPError as exc:
        code=exc.code
        exc.close()
        raise AccountReadError("http_"+str(code)) from None
    except AccountReadError:
        raise
    except (URLError,OSError,ValueError,HTTPException):
        raise AccountReadError("network_or_tls_error") from None
    if len(raw)>MAX_RESPONSE:
        raise AccountReadError("response_too_large")
    try:
        value=json.loads(raw,parse_float=Decimal,parse_constant=_no_constant,object_pairs_hook=_unique_object)
    except (ValueError,UnicodeError,RecursionError):
        raise AccountReadError("invalid_json_response") from None
    if not isinstance(value,dict):
        raise AccountReadError("unexpected_response_shape")
    evidence={"operation":operation,"received_ns":time.time_ns(),"duration_ns":time.monotonic_ns()-start,
              "body_sha256":hashlib.sha256(raw).hexdigest(),"raw_response_retained":False}
    return value,evidence


def native_name(value):
    if not isinstance(value,str) or not re.fullmatch(r"[A-Za-z0-9_]{1,40}",value):
        raise AccountReadError("invalid_native_asset_or_market")
    return value


def nonnegative(value):
    result=number(value)
    if result<0:
        raise AccountReadError("negative_spot_balance_or_fee")
    # Avoid rounded totals and pathological exponents from malformed responses.
    if len(result.as_tuple().digits)>28 or abs(result.as_tuple().exponent)>28:
        raise AccountReadError("unsupported_monetary_precision")
    return result


def boolean_or_unknown(value):
    return value if type(value) is bool else None


def distinct_balances(rows):
    seen=set()
    for row in rows:
        asset=row["native_asset"].upper()
        if asset in seen:
            raise AccountReadError("duplicate_native_asset")
        seen.add(asset)
    return rows


def exact_sum(a, b):
    from decimal import localcontext
    with localcontext() as context:
        context.prec=100
        return a+b


def parse_account(venue: str, operation: str, value: dict) -> dict:
    """Whitelist fields. Preserve unknown units/roles; do not invent usable funds."""
    if venue not in READS or operation not in READS[venue]:
        raise AccountReadError("operation_not_in_read_allowlist")
    if not isinstance(value,dict):
        raise AccountReadError("unexpected_response_shape")
    if venue == "nobitex":
        if value.get("status") != "ok":
            raise AccountReadError("venue_rejected_read")
        if operation == "profile":
            profile=value["profile"]
            if not isinstance(profile,dict):
                raise AccountReadError("unexpected_profile_shape")
            options=profile.get("options",{})
            if not isinstance(options,dict):
                raise AccountReadError("unexpected_options_shape")
            fees=[]
            for key,market in (("fee","IRR_markets"),("feeUsdt","USDT_markets")):
                if key in options:
                    fees.append({"scope":market,"reported_field":key,"reported_value":nonnegative(options[key]),
                                 "normalized_rate":None,"unit":"unverified","liquidity_role":"unverified"})
            return {"fee_observations":fees,"fee_rates_usable":False,
                    "fee_status":"unit_and_maker_taker_mapping_unverified" if fees else "missing"}
        rows=value["wallets"]
        if not isinstance(rows,list):
            raise AccountReadError("unexpected_wallet_list")
        balances=[]
        for row in rows:
            total=nonnegative(row["balance"])
            locked=nonnegative(row["blockedBalance"]) if "blockedBalance" in row else None
            free=nonnegative(row["activeBalance"]) if "activeBalance" in row else None
            consistent=(locked is not None and free is not None and total==exact_sum(locked,free))
            balances.append({"native_asset":native_name(row["currency"]),"reported_total":total,
                             "reported_free":free,"reported_locked":locked,
                             "balance_state":"internally_consistent" if consistent else "unverified_or_inconsistent"})
        return {"wallet_type":"spot","balances":distinct_balances(balances)}
    if venue == "wallex":
        if value.get("success") is not True:
            raise AccountReadError("venue_rejected_read")
        result=value["result"]
        if not isinstance(result,dict):
            raise AccountReadError("unexpected_wallex_result")
        if operation == "fees":
            fees,unparsed,seen=[],0,set()
            for symbol,row in result.items():
                fields={"makerFeeRate","takerFeeRate"}
                present=fields.intersection(row) if isinstance(row,dict) else set()
                if not present:
                    # The observed response also contains non-fee-shaped entries.
                    # Do not infer their purpose, rate or market applicability.
                    unparsed+=1
                    continue
                if present != fields:
                    raise AccountReadError("incomplete_market_fee_fields")
                native=native_name(symbol)
                if native.upper() in seen:
                    raise AccountReadError("duplicate_native_fee_market")
                seen.add(native.upper())
                maker,taker=nonnegative(row["makerFeeRate"]),nonnegative(row["takerFeeRate"])
                if max(maker,taker)>=1:
                    raise AccountReadError("fee_ratio_out_of_range")
                fees.append({"native_market":native,"maker_rate":maker,"taker_rate":taker,
                             "unit":"ratio","charged_asset":"unverified"})
            if unparsed and not fees:
                raise AccountReadError("unrecognized_fee_mapping")
            return {"fee_observations":fees,"fee_rates_usable":bool(fees),
                    "unparsed_entry_count":unparsed,"all_result_entries_parsed":not unparsed,
                    "market_coverage_verified":False,
                    "fee_status":"current_reported_ratios_fee_asset_unverified" if fees else "missing"}
        rows=result["balances"]
        if not isinstance(rows,dict):
            raise AccountReadError("unexpected_wallet_mapping")
        return {"wallet_type":"main","balances":distinct_balances([{"native_asset":native_name(asset),
                "reported_value":nonnegative(row["value"]),"reported_locked":nonnegative(row["locked"]),
                "free":None,"total":None,"balance_state":"value_includes_locked_unverified"} for asset,row in rows.items()])}
    if venue == "tabdeal":
        if value.get("accountType") != "SPOT" or not isinstance(value.get("balances"),list):
            raise AccountReadError("unexpected_spot_account_response")
        fees=[]
        for role,key in (("maker","makerCommission"),("taker","takerCommission")):
            if key in value:
                fees.append({"reported_field":key,"liquidity_role":role,"reported_value":nonnegative(value[key]),
                             "normalized_rate":None,"unit":"unverified"})
        balances=[]
        for row in value["balances"]:
            free,locked=nonnegative(row["free"]),nonnegative(row["freeze"])
            balances.append({"native_asset":native_name(row["asset"]),"free":free,"locked":locked,"total":exact_sum(free,locked)})
        return {"wallet_type":"spot","balances":distinct_balances(balances),"fee_observations":fees,"fee_rates_usable":False,
                "fee_status":"commission_unit_unverified" if fees else "missing",
                "reported_capabilities":{key:boolean_or_unknown(value.get(key)) for key in ("canTrade","canWithdraw","canDeposit")},
                "capability_scope":"account_response_not_proof_of_key_permissions"}
    raise AccountReadError("unsupported_venue")


def fee_schema_shape(value, depth=0):
    """Bounded types/allowlisted labels only; no account values or arbitrary keys.

    A failed fee parser needs structural evidence without retaining the response.
    Unknown keys may themselves contain private data, so never copy them.
    """
    labels={"success","result","data","fees","fee","markets","symbol","market",
            "makerFeeRate","takerFeeRate","maker_fee_rate","taker_fee_rate",
            "makerFee","takerFee","maker_fee","taker_fee","maker","taker",
            "makerCommission","takerCommission","recent_days_sum","level","tier",
            "spot","margin","type","status"}
    kind=("null" if value is None else "boolean" if type(value) is bool else
          "object" if isinstance(value,dict) else "array" if isinstance(value,list) else
          "string" if isinstance(value,str) else "number" if isinstance(value,(int,Decimal)) else "other")
    result={"type":kind}
    if kind not in ("object","array"):
        return result
    result["length"]=len(value)
    if depth>=4:
        result["truncated"]=bool(value)
        return result
    groups={}
    items=value.items() if kind=="object" else enumerate(value)
    visited=0
    for key,child in items:
        if visited>=512:
            break
        visited+=1
        item={"label":key if kind=="object" and key in labels else "<other>",
              "shape":fee_schema_shape(child,depth+1)}
        fingerprint=json.dumps(item,sort_keys=True,separators=(",",":"))
        if fingerprint not in groups and len(groups)>=32:
            continue
        groups.setdefault(fingerprint,{**item,"count":0})["count"]+=1
    result["groups"]=list(groups.values())
    result["truncated"]=visited<len(value) or sum(g["count"] for g in groups.values())<visited
    return result


def inspect_account(venue: str, credentials: Credentials) -> dict:
    if venue not in READS:
        raise AccountReadError("unsupported_venue")
    results=[]
    for operation in READS[venue]:
        raw,evidence=None,None
        try:
            raw,evidence=read_operation(venue,operation,credentials)
            normalized=parse_account(venue,operation,raw)
            results.append({"operation":operation,"status":"accepted","data":normalized,"evidence":evidence})
        except AccountReadError as exc:
            results.append({"operation":operation,"status":"unavailable","reason":str(exc)})
        except (ValueError,TypeError,KeyError,ArithmeticError,AttributeError):
            results.append({"operation":operation,"status":"unavailable","reason":"unrecognized_response_schema"})
        if operation=="fees" and raw is not None and results[-1]["status"]=="unavailable":
            results[-1]["diagnostic"]={"kind":"redacted_fee_schema","shape":fee_schema_shape(raw)}
            results[-1]["evidence"]=evidence
    return {"schema":1,"kind":"private_account_read_assessment","venue":venue,"observed_ns":time.time_ns(),
            "client_version":__version__,"parser_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "results":results,"all_requested_reads_accepted":all(r["status"]=="accepted" for r in results),
            "key_permissions_verified":False,"order_execution_verified":False,"live_trading_enabled":False,
            "paper_capital_modified":False,"credentials_stored":False,"balances_allocated":False}


def save_assessment(report: dict) -> Path:
    """Only normalized reports, exclusively created under this checkout's ignored .local."""
    root=Path(__file__).resolve().parent.parent
    venue=report["venue"]
    if venue not in READS or report.get("kind") != "private_account_read_assessment":
        raise AccountReadError("invalid_assessment")
    directory=root
    for name in (".local","accounts"):
        directory=directory/name
        # Refuse existing links/junctions before creating or following directories.
        if directory.is_symlink() or (hasattr(directory,"is_junction") and directory.is_junction()):
            raise AccountReadError("private_directory_redirected")
        directory.mkdir(mode=0o700,exist_ok=True)
        if directory.resolve() != root.joinpath(*directory.relative_to(root).parts):
            raise AccountReadError("private_directory_redirected")
    path=directory/(venue+"-"+uuid4().hex+".json")
    payload=json.dumps(report,ensure_ascii=True,default=str,allow_nan=False,indent=2)+"\n"
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,"w",encoding="utf-8",newline="\n") as stream:
        stream.write(payload)
    return path


def account_check(venue: str, *, legacy_token=False) -> int:
    """Interactive boundary: no secret arguments, raw errors, balances or profile output."""
    try:
        if legacy_token and venue != "nobitex":
            raise AccountReadError("legacy_token_only_for_nobitex")
        credentials=prompt_credentials(venue,"nobitex_token" if legacy_token else None)
        try:
            report=inspect_account(venue,credentials)
        finally:
            del credentials  # No persistence; Python strings cannot promise memory zeroization.
        path=save_assessment(report)
        print(json.dumps({"kind":"account_read_summary","venue":venue,
                          "operations":[{k:v for k,v in r.items() if k in ("operation","status","reason")} for r in report["results"]],
                          "private_report":".local/accounts/"+path.name,
                          "live_trading_enabled":False,"credentials_stored":False}))
        return int(not report["all_requested_reads_accepted"])
    except (KeyboardInterrupt,EOFError):
        reason="input_cancelled"
    except AccountReadError as exc:
        reason=str(exc)
    except Exception:
        # Account HTTP libraries may include signed URLs or bodies in exceptions.
        reason="account_check_failed_details_redacted"
    print(json.dumps({"kind":"account_read_unavailable","reason":reason,"live_trading_enabled":False}))
    return 1
