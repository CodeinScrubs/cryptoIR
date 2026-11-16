"""Public leveraged-product observations; never spot books or executable orders."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from .domain import DataError, Level, Book, number
from .feeds import public_response, NoRedirect

CATALOGS={
    'nobitex_margin':'https://apiv2.nobitex.ir/margin/markets/list',
    'wallex_margin':'https://api.wallex.ir/margin-trade/v1/public/markets',
    'tabdeal_fapi':'https://api1.tabdeal.org/r/fapi/v1/exchangeInfo',
}
TABDEAL_DEPTH='https://api1.tabdeal.org/r/fapi/v1/depth'
WALLEX_CALCULATIONS={name:'https://api.wallex.ir/margin-trade/v1/public/'+name for name in ('loan','dry-run')}


def encoded(value):
    return json.dumps(value,default=str,ensure_ascii=True,allow_nan=False,separators=(',',':'))


def identifier(value):
    if not isinstance(value,str) or not re.fullmatch('[A-Za-z0-9_]{1,40}',value):
        raise DataError('invalid native product identifier')
    return value


def numeric(value, *, positive=False):
    n=number(value)
    if n<0 or (positive and n==0) or len(n.as_tuple().digits)>28 or abs(n.as_tuple().exponent)>28:
        raise DataError('invalid product number or unsupported precision')
    return n


def optional_number(row,key):
    return None if row.get(key) is None else numeric(row[key])


def optional_flag(row,key):
    value=row.get(key)
    if value is not None and type(value) is not bool:
        raise DataError('invalid product flag')
    return value


def precision(row,key):
    n=row[key]
    if type(n) is not int or not 0<=n<=30:
        raise DataError('invalid reported precision')
    return n


def strict_json(raw):
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:
                raise DataError('duplicate JSON field')
            result[key]=value
        return result
    def no_constant(_):
        raise DataError('nonfinite JSON value')
    return json.loads(raw,parse_float=Decimal,object_pairs_hook=unique,parse_constant=no_constant)


def checked_payload(response,url):
    if response.get('schema')!=1 or response.get('kind')!='snapshot' or response.get('url')!=url or response.get('status_code')!=200:
        raise DataError('unavailable or mismatched public response')
    raw=response['raw_body']
    if not isinstance(raw,str) or hashlib.sha256(raw.encode()).hexdigest()!=response['body_sha256']:
        raise DataError('public response hash mismatch')
    if any(type(response[k]) is not int or response[k]<0 for k in ('started_ns','received_ns','duration_ns')) or response['started_ns']==0:
        raise DataError('invalid public observation time')
    if response['received_ns']<response['started_ns'] or abs(response['received_ns']-response['started_ns']-response['duration_ns'])>1_000_000_000:
        raise DataError('public observation clock discontinuity')
    payload=strict_json(raw)
    if encoded(payload)!=encoded(response['payload']):
        raise DataError('public payload differs from raw response')
    return payload


def parse_products(family,payload):
    if family not in CATALOGS or not isinstance(payload,dict):
        raise DataError('unsupported product family or schema')
    if family=='nobitex_margin':
        if payload.get('status')!='ok' or not isinstance(payload.get('markets'),dict):
            raise DataError('invalid Nobitex margin catalog')
        rows=[dict(row,native_symbol=symbol) for symbol,row in payload['markets'].items()]
    elif family=='wallex_margin':
        if payload.get('success') is not True or not isinstance(payload.get('result'),list):
            raise DataError('invalid Wallex margin catalog')
        rows=payload['result']
    else:
        if not isinstance(payload.get('symbols'),list):
            raise DataError('invalid Tabdeal FAPI catalog')
        rows=payload['symbols']
    result=[]
    seen=set()
    for row in rows:
        symbol=identifier(row['native_symbol'] if family=='nobitex_margin' else row['symbol'])
        if symbol in seen:
            raise DataError('duplicate native product')
        seen.add(symbol)
        market={'product_id':family+':'+symbol,'family':family,'native_symbol':symbol,
                'account_verified':False,'execution_ready':False,'faithful_backtest_ready':False,
                'quantity_step':None,'price_tick':None,'minimum_notional':None,'contract_multiplier':None,
                'historical_rules_available':False,'maintenance_margin_schedule':None,
                'complete_cost_model':False,'liquidation_model_verified':False}
        if family=='nobitex_margin':
            market.update(native_base=identifier(row['srcCurrency']),native_quote=identifier(row['dstCurrency']),
                          public_max_leverage=numeric(row['maxLeverage'],positive=True),
                          long_enabled=optional_flag(row,'buyEnabled'),short_enabled=optional_flag(row,'sellEnabled'),
                          renewal_fields={k:optional_number(row,k) for k in ('positionFeeRate','sellPositionFeeRate','buyPositionFeeRate')},
                          renewal_schedule_verified=False,account_max_may_differ=True)
        elif family=='wallex_margin':
            low,high,step=(numeric(row[k],positive=True) for k in ('min_risk_coef','max_risk_coef','risk_coef_step'))
            if low>high:
                raise DataError('reversed credit ratio bounds')
            margin=numeric(row['liquidity_margin'])
            age=row['max_age']
            if margin>=1 or type(age) is not int or age<=0:
                raise DataError('invalid margin or maximum age')
            market.update(native_base=identifier(row['base']),native_quote=identifier(row['quote']),
                          native_credit_ratio={'minimum':low,'maximum':high,'step':step},
                          reported_liquidity_margin=margin,reported_max_age_seconds=age,
                          public_max_leverage=None,credit_ratio_is_not_cross_venue_leverage=True)
        else:
            status=identifier(row['status'])
            market.update(native_base=identifier(row['baseAsset']),native_quote=identifier(row['quoteAsset']),
                          status=status,public_max_leverage=None,
                          reported_precision={k:precision(row,k) for k in ('pricePrecision','quantityPrecision','quotePrecision')},
                          precision_is_not_order_step=True)
        result.append(market)
    return result


def catalog_record(family,response):
    record={'schema':1,'kind':'product_catalog','family':family,'response':response,'status':'unavailable'}
    try:
        record.update(markets=parse_products(family,checked_payload(response,CATALOGS[family])),status='observed')
    except (ValueError,KeyError,TypeError,AttributeError) as exc:
        record['reason']=str(exc) if isinstance(exc,DataError) else 'invalid_product_schema'
    return record


def resolve_product(catalog,pair):
    if catalog['status']!='observed' or catalog['family']!='tabdeal_fapi':
        raise DataError('observed FAPI catalog required')
    parts=pair.split('/')
    if len(parts)!=2 or any(identifier(p)!=p.upper() for p in parts) or parts[0]==parts[1]:
        raise DataError('explicit BASE/QUOTE pair required')
    matches=[row for row in catalog['markets'] if (row['native_base'],row['native_quote'])==tuple(parts)]
    if len(matches)!=1 or matches[0]['status']!='TRADING':
        raise DataError('missing, ambiguous or inactive FAPI market')
    return matches[0]


def product_book_record(product,response):
    symbol=identifier(product['native_symbol'])
    if product.get('family')!='tabdeal_fapi' or product.get('product_id')!='tabdeal_fapi:'+symbol or product.get('status')!='TRADING':
        raise DataError('active FAPI product required; spot and margin catalogs are distinct')
    url=TABDEAL_DEPTH+'?'+urlencode({'symbol':symbol,'limit':5})
    result={'schema':1,'kind':'product_book','family':'tabdeal_fapi','product_id':product['product_id'],
            'native_symbol':symbol,'response':response,'status':'unavailable'}
    try:
        payload=checked_payload(response,url)
        def levels(side):
            rows=payload[side]
            if not isinstance(rows,list) or any(not isinstance(r,list) or len(r)!=2 for r in rows):
                raise DataError('invalid product book levels')
            return tuple(Level(numeric(p,positive=True),numeric(q,positive=True)) for p,q in rows)
        bids,asks=levels('bids'),levels('asks')
        # Reuse structural checks only; never expose this as a tradable spot Book.
        Book('tabdeal_fapi',product['native_base']+'/'+product['native_quote'],bids,asks,
             response['received_ns'],response['duration_ns'])
        update=payload['lastUpdateId']
        if type(update) is not int or update<0:
            raise DataError('invalid product book update id')
        result.update(status='observed',bids=[[x.price,x.quantity] for x in bids],asks=[[x.price,x.quantity] for x in asks],
                      last_update_id=update,reported_event_time=optional_number(payload,'E'),exchange_ns=None,
                      source_freshness_verified=False,quantity_unit='native_quantity_not_contract_verified',
                      executable=False,spot_compatible=False)
    except (ValueError,KeyError,TypeError,AttributeError) as exc:
        result['reason']=str(exc) if isinstance(exc,DataError) else 'invalid_product_book_schema'
    return result


def collect_products(path: Path, tabdeal_pair=None):
    if tabdeal_pair is not None and (not isinstance(tabdeal_pair,str) or not tabdeal_pair):
        raise DataError('nonempty optional FAPI pair required')
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    # Reserve the artifact before network calls; never overwrite a prior observation.
    with path.open('x',encoding='utf-8') as stream:
        stream.write(encoded({'schema':1,'kind':'product_observation_start','families':list(CATALOGS),
                              'tabdeal_pair':tabdeal_pair})+'\n')
        def collect(family):
            return catalog_record(family,public_response(CATALOGS[family]))
        with ThreadPoolExecutor(max_workers=3) as pool:
            records=list(pool.map(collect,CATALOGS))
        for record in records:
            stream.write(encoded(record)+'\n')
        if tabdeal_pair:
            catalog=next(r for r in records if r['family']=='tabdeal_fapi')
            try:
                product=resolve_product(catalog,tabdeal_pair)
            except DataError as exc:
                record={'schema':1,'kind':'product_book_unavailable','family':'tabdeal_fapi',
                        'pair':tabdeal_pair,'status':'unavailable','reason':str(exc)}
            else:
                response=public_response(TABDEAL_DEPTH+'?'+urlencode({'symbol':product['native_symbol'],'limit':5}))
                record=product_book_record(product,response)
            records.append(record)
            stream.write(encoded(record)+'\n')
        stream.write(encoded({'schema':1,'kind':'product_observation_end'})+'\n')
    return {'kind':'product_observation_summary','catalogs':{
        r['family']:{'status':r['status'],'markets':len(r.get('markets',[]))} for r in records if r['kind']=='product_catalog'},
        'books_observed':sum(r['kind']=='product_book' and r['status']=='observed' for r in records),
        'all_requests_observed':all(r['status']=='observed' for r in records),
        'faithful_backtest_ready':False,'execution_ready':False}


def audit_products(path: Path):
    catalogs={}
    observed=unavailable=books=0
    lines=Path(path).read_text(encoding='utf-8').splitlines()
    if len(lines)<5:
        raise DataError('incomplete product artifact')
    header=strict_json(lines[0]);footer=strict_json(lines[-1])
    pair=header.get('tabdeal_pair')
    if (encoded(header)!=encoded({'schema':1,'kind':'product_observation_start','families':list(CATALOGS),'tabdeal_pair':pair})
            or (pair is not None and (not isinstance(pair,str) or not pair))
            or footer!={'schema':1,'kind':'product_observation_end'}):
        raise DataError('invalid product artifact header or missing end')
    for line in lines[1:-1]:
        row=strict_json(line)
        if row.get('schema')!=1:
            raise DataError('invalid product artifact schema')
        if row['kind']=='product_catalog':
            family=row['family']
            if family not in CATALOGS or family in catalogs:
                raise DataError('unknown or duplicate product family')
            rebuilt=catalog_record(family,row['response'])
            catalogs[family]=rebuilt
        elif row['kind']=='product_book':
            catalog=catalogs.get('tabdeal_fapi',{})
            if not pair or not catalog:
                raise DataError('book has no preceding catalog or request')
            product=resolve_product(catalog,pair)
            rebuilt=product_book_record(product,row['response'])
            books+=1
        elif row['kind']=='product_book_unavailable':
            if not pair or 'tabdeal_fapi' not in catalogs:
                raise DataError('unavailable book has no preceding request')
            try:
                resolve_product(catalogs['tabdeal_fapi'],pair)
            except DataError as exc:
                rebuilt={'schema':1,'kind':'product_book_unavailable','family':'tabdeal_fapi',
                         'pair':pair,'status':'unavailable','reason':str(exc)}
            else:
                raise DataError('cannot reconstruct rejected book selection')
            books+=1
        else:
            raise DataError('unknown product artifact kind')
        if encoded(row)!=encoded(rebuilt):
            raise DataError('product artifact does not reconstruct from raw response')
        observed+=row['status']=='observed'
        unavailable+=row['status']!='observed'
    if set(catalogs)!=set(CATALOGS):
        raise DataError('incomplete product catalog artifact')
    if books!=int(pair is not None):
        raise DataError('missing or repeated requested product book')
    return {'kind':'product_observation_audit','observed_records':observed,'unavailable_records':unavailable,
            'book_records':books,'sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest(),
            'all_requests_observed':unavailable==0,'historical_coverage_verified':False,'execution_ready':False}


def calculation_request(operation,request):
    if operation not in WALLEX_CALCULATIONS:
        raise DataError('not an allowed public calculation')
    allowed={'market','risk_coef','side','collateral','open_price'}
    if set(request)-allowed or not {'market','risk_coef','side'}<=set(request):
        raise DataError('invalid calculation fields')
    identifier(request['market'])
    if request['side'] not in ('long','short'):
        raise DataError('invalid side')
    for key in set(request)-{'market','side'}:
        numeric(request[key],positive=True)
    if operation=='dry-run' and set(request)!=allowed:
        raise DataError('dry-run requires collateral and open price')
    return Request(WALLEX_CALCULATIONS[operation],data=encoded(request).encode(),
                   headers={'Content-Type':'application/json','Accept':'application/json','User-Agent':'cryptoIR-public-calculation'},method='POST')


def public_calculation(operation,request):
    req=calculation_request(operation,request)
    start,mono=time.time_ns(),time.monotonic_ns()
    result={'schema':1,'url':req.full_url,'method':'POST','request':request,'started_ns':start,
            'purpose':'public_calculation_only_no_position_created'}
    try:
        try:
            response=build_opener(NoRedirect).open(req,timeout=10)
        except HTTPError as exc:
            response=exc  # Public validation errors; no credential is ever sent.
        with response:
            raw=response.read(2*1024*1024+1)
            code=response.code
        if len(raw)>2*1024*1024:
            raise DataError('public calculation exceeds size limit')
        result.update(kind='snapshot' if code==200 else 'http_error',status_code=code,
                      received_ns=time.time_ns(),duration_ns=time.monotonic_ns()-mono,
                      raw_body=raw.decode(),body_sha256=hashlib.sha256(raw).hexdigest(),payload=strict_json(raw))
        if abs(result['received_ns']-start-result['duration_ns'])>1_000_000_000:
            raise DataError('wall clock changed during calculation')
    except Exception as exc:
        result.update(kind='error',error_type=type(exc).__name__,received_ns=time.time_ns())
    return result


def parse_quote(response,request):
    value=checked_payload(response,WALLEX_CALCULATIONS['dry-run'])
    if response.get('method')!='POST' or encoded(response.get('request'))!=encoded(request) or value.get('success') is not True:
        raise DataError('quote request or response mismatch')
    row=value['result']
    if row['market']!=request['market'] or row['side']!=request['side']:
        raise DataError('quote market or side mismatch')
    for key in ('open_price','risk_coef'):
        if numeric(row[key],positive=True)!=numeric(request[key],positive=True):
            raise DataError('quote changed requested terms')
    amounts={}
    for key in ('collateral','loan','trade_fee','open_fee','extend_fee'):
        amounts[key]={'value':numeric(row[key]['value']), 'native_currency':identifier(row[key]['currency'])}
    if amounts['collateral']['value']!=numeric(request['collateral'],positive=True):
        raise DataError('quote changed collateral')
    times={}
    for key in ('max_age','extend_time'):
        if type(row[key]) is not int or row[key]<=0:
            raise DataError('invalid quoted duration')
        times[key+'_seconds']=row[key]
    opening=numeric(row['open_price'],positive=True)
    call=numeric(row['call_price'],positive=True)
    liquidation=numeric(row['liquidity_price'],positive=True)
    if not (liquidation<call<opening if request['side']=='long' else liquidation>call>opening):
        raise DataError('inconsistent quoted risk prices')
    with localcontext() as context:
        context.prec=60
        distance=abs(liquidation/opening-1)
    return {'market':row['market'],'side':row['side'],'native_credit_ratio':numeric(row['risk_coef']),
            'open_price':opening,'amounts':amounts,**times,'quoted_call_price':call,
            'quoted_liquidation_price':liquidation,'quoted_liquidation_distance_ratio':distance,
            'quote_is_position':False,'fees_applied_to_paper':False,'future_liquidation_price_guaranteed':False,
            'exit_fee':None,'historical_cost_model_verified':False,'execution_ready':False}


def margin_quote(path: Path,market,side,collateral,risk_coef,open_price):
    request={'market':market,'side':side,'collateral':str(numeric(collateral,positive=True)),
             'risk_coef':str(numeric(risk_coef,positive=True)),'open_price':str(numeric(open_price,positive=True))}
    calculation_request('dry-run',request)  # Validate before creating the output.
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as stream:
        record={'schema':1,'kind':'public_margin_quote','venue':'wallex','request':request,'status':'unavailable'}
        record['response']=public_calculation('dry-run',request)
        try:
            record.update(status='observed',quote=parse_quote(record['response'],request))
        except (ValueError,KeyError,TypeError,AttributeError) as exc:
            record['reason']=str(exc) if isinstance(exc,DataError) else 'invalid_quote_schema'
        stream.write(encoded(record)+'\n')
    return {'kind':'public_margin_quote_summary','status':record['status'],'position_created':False,
            'execution_ready':False,'output':str(path)}


def verified_quote(raw: bytes):
    """Reconstruct one immutable in-memory artifact before any downstream use."""
    record=strict_json(raw)
    if record.get('schema')!=1 or record.get('kind')!='public_margin_quote' or record.get('venue')!='wallex':
        raise DataError('invalid public quote artifact')
    calculation_request('dry-run',record['request'])
    quote=parse_quote(record['response'],record['request'])
    if record['status']!='observed' or encoded(record['quote'])!=encoded(quote):
        raise DataError('quote does not reconstruct from raw response')
    return record,quote


def audit_quote(path: Path):
    raw=Path(path).read_bytes()
    verified_quote(raw)
    return {'kind':'public_margin_quote_audit','status':'observed','sha256':hashlib.sha256(raw).hexdigest(),
            'position_created':False,'execution_ready':False}
