import os
import hmac
import secrets
from pathlib import Path
from threading import RLock
from typing import Literal
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm.exc import StaleDataError
from .db import Session, Stream
from .keitaro import client, UpstreamError
from .domain import fingerprint, redistribute, merge_remote

app = FastAPI(title='Adrobot', version='1.0.0')
static = Path(__file__).parent / 'static'
app.mount('/assets', StaticFiles(directory=static), name='assets')
# One worker serializes local fetch/edit/push. DB versioning also rejects stale writes.
lock = RLock()

def auth(request: Request):
    expected = os.environ.get('APP_TOKEN', '')
    got = request.headers.get('Authorization', '').removeprefix('Bearer ')
    if not expected or not hmac.compare_digest(got, expected):
        raise HTTPException(401, 'Введите токен доступа к приложению.')

@app.middleware('http')
async def headers(request, call_next):
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'"
    return response

@app.exception_handler(UpstreamError)
def upstream(request, error):
    return JSONResponse(status_code=502, content={'detail': error.message, 'uncertain': error.uncertain})

@app.exception_handler(StaleDataError)
def stale(request, error):
    return JSONResponse(status_code=409, content={'detail': 'Состояние изменено в другой вкладке. Обновите страницу.'})

@app.get('/')
def index(): return FileResponse(static / 'index.html')

@app.get('/api/catalog', dependencies=[Depends(auth)])
def catalog():
    def compact(path):
        return [{'id': x['id'], 'name': x['name']} for x in client.get(path) if x.get('state', 'active') == 'active']
    return {'offers': compact('offers'), 'domains': compact('domains'), 'groups': compact('groups?type=campaigns'), 'sources': compact('traffic_sources'), 'prefix': prefix()}

def prefix(): return os.environ.get('CAMPAIGN_PREFIX', 'test_kashintsev_')

def campaign(cid):
    c = client.get(f'campaigns/{cid}')
    if not c['name'].startswith(prefix()):
        raise HTTPException(403, 'Изменять можно только кампании с вашим тестовым префиксом.')
    return {k: c[k] for k in ['id','name','domain_id','group_id','traffic_source_id','state']}

@app.get('/api/campaigns', dependencies=[Depends(auth)])
def campaigns():
    return [{k: c[k] for k in ['id','name','state']} for c in client.get('campaigns') if c['name'].startswith(prefix()) and c.get('state') != 'deleted']

class Create(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    countries: list[str] = Field(min_length=1, max_length=50)
    offer_id: int = Field(gt=0)
    domain_id: int = Field(gt=0)
    group_id: int = Field(gt=0)
    source_id: int = Field(gt=0)

@app.post('/api/campaigns', dependencies=[Depends(auth)], status_code=201)
def create(body: Create):
    name = body.name.strip()
    if not name: raise HTTPException(422, 'Укажите название кампании.')
    countries = list(dict.fromkeys(x.strip().upper() for x in body.countries))
    if any(len(x) != 2 or not x.isascii() or not x.isalpha() for x in countries):
        raise HTTPException(422, 'Страны задаются двухбуквенными ISO-кодами, например AU, DE.')
    data = catalog()
    for field, value in [('offers',body.offer_id),('domains',body.domain_id),('groups',body.group_id),('sources',body.source_id)]:
        if value not in {x['id'] for x in data[field]}: raise HTTPException(422, f'Недоступное значение: {field}. Обновите справочники.')
    if not name.startswith(prefix()): name = prefix() + name
    created = client.post('campaigns', {'name':name, 'alias':secrets.token_hex(8), 'type':'position', 'domain_id':body.domain_id, 'group_id':body.group_id, 'traffic_source_id':body.source_id, 'state':'disabled'})
    cid = created['id']
    try:
        client.post('streams', {'campaign_id':cid,'type':'forced','name':', '.join(countries)+' → Google','position':1,'schema':'redirect','action_type':'http','action_payload':'https://www.google.com/','collect_clicks':True,'filters':[{'name':'country','mode':'accept','payload':countries}]})
        client.post('streams', {'campaign_id':cid,'type':'default','name':'Остальной трафик → офферы','position':2,'schema':'landings','action_type':'http','collect_clicks':True,'offers':[{'offer_id':body.offer_id,'share':100,'state':'active'}]})
        client.put(f'campaigns/{cid}', {'state':'active'})
    except UpstreamError as e:
        # Retain disabled partial campaign for inspection; never retry a timed-out POST.
        raise HTTPException(502, f'Кампания #{cid} создана частично. Проверьте её в Keitaro перед повтором. '+e.message)
    return campaign(cid)

def view(s):
    return {'id':s.id,'campaign_id':s.campaign_id,'name':s.name,'version':s.version,'offers':s.draft,'editable':bool(s.remote.get('offers') or s.baseline or s.draft),'dirty':s.draft!=s.baseline,'base_hash':fingerprint(s.remote)}

@app.get('/api/campaigns/{cid}', dependencies=[Depends(auth)])
def detail(cid:int):
    c=campaign(cid)
    with Session() as db:
        c['streams']=[view(s) for s in db.scalars(select(Stream).where(Stream.campaign_id==cid))]
    return c

class Fetch(BaseModel):
    discard: bool = False

@app.post('/api/campaigns/{cid}/fetch', dependencies=[Depends(auth)])
def fetch(cid:int, body:Fetch):
    c=campaign(cid)
    with lock, Session() as db:
        existing=list(db.scalars(select(Stream).where(Stream.campaign_id==cid)))
        if not body.discard and any(s.draft!=s.baseline for s in existing):
            raise HTTPException(409,'Есть черновики. Опубликуйте или отмените их перед загрузкой.')
        remote=client.get(f'campaigns/{cid}/streams')
        seen=set()
        for r in remote:
            seen.add(r['id']); s=db.get(Stream,r['id'])
            old=s.baseline if s else []
            links=merge_remote(r.get('offers',[]),old)
            if s:
                s.name=r['name'];s.remote=r;s.baseline=links;s.draft=links;s.version+=1
            else: db.add(Stream(id=r['id'],campaign_id=cid,name=r['name'],remote=r,baseline=links,draft=links))
        for s in existing:
            if s.id not in seen: db.delete(s)
        db.commit()
    return detail(cid)

class Edit(BaseModel):
    version:int
    operation:Literal['add','remove','restore','pin','unpin','cancel']
    offer_id:int=Field(default=0,ge=0)
    share:int=Field(default=0,ge=0,le=100)

@app.post('/api/streams/{sid}/edit', dependencies=[Depends(auth)])
def edit(sid:int, body:Edit):
    with lock, Session() as db:
        s=db.get(Stream,sid)
        if not s: raise HTTPException(404,'Сначала загрузите потоки.')
        campaign(s.campaign_id)
        if s.version!=body.version: raise HTTPException(409,'Черновик изменён в другой вкладке. Обновите страницу.')
        if body.operation=='cancel': s.draft=s.baseline
        else:
            if not (s.remote.get('offers') or s.baseline or s.draft): raise HTTPException(422,'Поток без офферов доступен только для чтения.')
            items=[dict(x) for x in s.draft]; item=next((x for x in items if x['offer_id']==body.offer_id),None)
            if body.operation=='add':
                if item: raise HTTPException(422,'Оффер уже есть. Для архивного используйте «Вернуть».')
                offers=client.get('offers')
                if body.offer_id not in {x['id'] for x in offers if x.get('state')=='active'}: raise HTTPException(422,'Такого активного оффера нет в Keitaro.')
                items.append({'offer_id':body.offer_id,'share':0,'removed':False,'pinned':False})
            else:
                if not item: raise HTTPException(404,'Оффер не найден в потоке.')
                if body.operation=='remove': item['removed']=True;item['pinned']=False
                if body.operation=='restore': item['removed']=False;item['pinned']=False
                if body.operation in ('pin','unpin'):
                    if item['removed']: raise HTTPException(422,'Сначала восстановите оффер.')
                    item['pinned']=body.operation=='pin';item['share']=body.share if body.operation=='pin' else item['share']
            try: s.draft=redistribute(items)
            except ValueError as e: raise HTTPException(422,str(e)) from None
        s.version+=1;db.commit();return view(s)

class Push(BaseModel):
    version:int

@app.post('/api/streams/{sid}/push', dependencies=[Depends(auth)])
def push(sid:int, body:Push):
    with lock, Session() as db:
        s=db.get(Stream,sid)
        if not s: raise HTTPException(404,'Поток не найден.')
        campaign(s.campaign_id)
        if s.version!=body.version: raise HTTPException(409,'Черновик изменён. Обновите страницу.')
        if s.draft==s.baseline: return view(s)
        current=client.get(f'streams/{sid}')
        if fingerprint(current)!=fingerprint(s.remote): raise HTTPException(409,'Поток изменён в Keitaro. Отмените черновик и загрузите потоки заново.')
        try: normalized=redistribute(s.draft)
        except ValueError as e: raise HTTPException(422,str(e)) from None
        offers=[{'offer_id':x['offer_id'],'share':x['share'],'state':'active'} for x in normalized if not x['removed']]
        # PUT only links, never replace filters, actions, landings or stream settings.
        client.put(f'streams/{sid}', {'offers':offers})
        after=client.get(f'streams/{sid}')
        got=sorted((x['offer_id'],int(x['share'])) for x in after.get('offers',[]) if x.get('state','active')=='active')
        if got!=sorted((x['offer_id'],x['share']) for x in offers):
            raise HTTPException(502,'Keitaro сохранил другое состояние. Загрузите потоки и проверьте изменения.')
        s.remote=after;s.baseline=normalized;s.draft=normalized;s.version+=1;db.commit();return view(s)
