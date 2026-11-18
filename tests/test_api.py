import copy,os
os.environ['APP_TOKEN']='test-token'
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app import main
from app.db import Base

class Fake:
    def __init__(self):
        self.c={'id':1,'name':'test_kashintsev_test','domain_id':1,'group_id':1,'traffic_source_id':1,'state':'active'}
        self.s={'id':10,'campaign_id':1,'name':'Offers','offers':[{'offer_id':1,'share':50,'state':'active'},{'offer_id':2,'share':50,'state':'active'}],'filters':[],'schema':'landings','action_type':'http'}
        self.readonly={'id':11,'campaign_id':1,'name':'Geo','offers':[],'filters':[{'name':'country','payload':['AU']}],'schema':'redirect','action_payload':'https://www.google.com/'}
        self.writes=[]
    def get(self,path):
        if path=='campaigns/1':return copy.deepcopy(self.c)
        if path=='campaigns':return [copy.deepcopy(self.c)]
        if path=='campaigns/1/streams':return copy.deepcopy([self.s,self.readonly])
        if path=='streams/10':return copy.deepcopy(self.s)
        if path in ['offers','domains','groups?type=campaigns','traffic_sources']:return [{'id':1,'name':'One','state':'active'},{'id':2,'name':'Two','state':'active'},{'id':3,'name':'Three','state':'active'}]
        raise AssertionError(path)
    def put(self,path,body):
        assert path=='streams/10';self.writes.append(copy.deepcopy(body));self.s.update(copy.deepcopy(body));return self.s

import pytest
@pytest.fixture
def env(monkeypatch):
    e=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool);Base.metadata.create_all(e)
    monkeypatch.setattr(main,'Session',sessionmaker(e,expire_on_commit=False));f=Fake();monkeypatch.setattr(main,'client',f)
    with TestClient(main.app) as c:
        c.headers['Authorization']='Bearer test-token';yield c,f
    e.dispose()

def fetch(c):return c.post('/api/campaigns/1/fetch',json={}).json()['streams'][0]
def edit(c,s,op,oid=0,share=0):return c.post('/api/streams/10/edit',json={'version':s['version'],'operation':op,'offer_id':oid,'share':share})

def test_auth_required(env):
    c,f=env;c.headers.clear();assert c.get('/api/campaigns').status_code==401

def test_draft_cancel_push_archive_restore(env):
    c,f=env;s=fetch(c);s=edit(c,s,'add',3).json();assert [o['share'] for o in s['offers']]==[34,33,33];assert not f.writes
    s=edit(c,s,'cancel').json();assert len(s['offers'])==2
    s=edit(c,s,'remove',2).json();assert s['offers'][1]['removed'];assert s['offers'][0]['share']==100
    s=c.post('/api/streams/10/push',json={'version':s['version']}).json();assert not s['dirty'];assert f.writes==[{'offers':[{'offer_id':1,'share':100,'state':'active'}]}]
    s=edit(c,s,'restore',2).json();assert [o['share'] for o in s['offers']]==[50,50]

def test_external_conflict_and_dirty_fetch(env):
    c,f=env;s=fetch(c);s=edit(c,s,'add',3).json();f.s['name']='Changed externally'
    assert c.post('/api/streams/10/push',json={'version':s['version']}).status_code==409
    assert c.post('/api/campaigns/1/fetch',json={}).status_code==409
    out=c.post('/api/campaigns/1/fetch',json={'discard':True});assert out.status_code==200;assert not out.json()['streams'][0]['dirty'];assert not f.writes

def test_stale_version_rejected(env):
    c,f=env;s=fetch(c);assert edit(c,s,'add',3).status_code==200;assert edit(c,s,'remove',2).status_code==409

def test_readonly_and_other_campaign_protected(env):
    c,f=env;fetch(c)
    assert c.post('/api/streams/11/edit',json={'version':1,'operation':'add','offer_id':3}).status_code==422
    f.c['name']='someone_else';assert c.get('/api/campaigns/1').status_code==403

def test_remote_removed_offer_kept_in_archive(env):
    c,f=env;fetch(c);f.s['offers']=[{'offer_id':1,'share':100,'state':'active'}]
    s=fetch(c);assert s['offers'][1]['removed'];assert not s['dirty']

def test_creation_configures_two_flows(env,monkeypatch):
    c,f=env;calls=[]
    def post(path,body):
        calls.append((path,copy.deepcopy(body)))
        return {'id':1}
    def put(path,body): calls.append((path,copy.deepcopy(body)));f.c.update(body);return f.c
    monkeypatch.setattr(f,'post',post,raising=False);monkeypatch.setattr(f,'put',put)
    r=c.post('/api/campaigns',json={'name':'launch','countries':['au','DE','AU'],'offer_id':1,'domain_id':1,'group_id':1,'source_id':1})
    assert r.status_code==201
    assert calls[0][1]['name']=='test_kashintsev_launch'
    assert calls[0][1]['state']=='disabled'
    a,b=calls[1][1],calls[2][1]
    assert a['type']=='forced' and a['filters'][0]['payload']==['AU','DE']
    assert a['action_payload']=='https://www.google.com/'
    assert b['type']=='default' and b['offers']==[{'offer_id':1,'share':100,'state':'active'}]
    assert calls[3]==('campaigns/1',{'state':'active'})

def test_invalid_geo_rejected_before_writes(env):
    c,f=env
    r=c.post('/api/campaigns',json={'name':'launch','countries':['AUS'],'offer_id':1,'domain_id':1,'group_id':1,'source_id':1})
    assert r.status_code==422

def test_failed_upstream_push_keeps_draft(env,monkeypatch):
    from app.keitaro import UpstreamError
    c,f=env;s=fetch(c);s=edit(c,s,'add',3).json()
    def fail(*a):raise UpstreamError('Timeout',uncertain=True)
    monkeypatch.setattr(f,'put',fail)
    r=c.post('/api/streams/10/push',json={'version':s['version']})
    assert r.status_code==502 and r.json()['uncertain']
    saved=c.get('/api/campaigns/1').json()['streams'][0]
    assert saved['dirty'] and saved['offers']==s['offers']

def test_draft_survives_session_and_cancel_restores_pin(env):
    c,f=env;s=fetch(c);s=edit(c,s,'pin',1,25).json()
    assert [x['share'] for x in s['offers']]==[25,75]
    saved=c.get('/api/campaigns/1').json()['streams'][0];assert saved['offers']==s['offers']
    s=edit(c,saved,'cancel').json();assert [x['share'] for x in s['offers']]==[50,50]
    assert not s['offers'][0]['pinned']

def test_all_remote_links_removed_can_be_restored(env):
    c,f=env;fetch(c);f.s['offers']=[]
    s=fetch(c);assert s['editable'] and all(x['removed'] for x in s['offers'])
    r=edit(c,s,'restore',1);assert r.status_code==200
    assert r.json()['offers'][0]['share']==100
