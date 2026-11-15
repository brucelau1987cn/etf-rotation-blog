import importlib.util,json,pathlib,sys
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))

def load():
 import recalibrate_dragon_snapshots as m
 return m

def fixture(tmp_path):
 p=tmp_path/'2026-11-13.json';data={'data_as_of':'2026-11-13','intersection':['600000.SH'],'enrichments':{'600000.SH':{'other':{'preserve':True},'jinlong':{'old':True}}},'other_contract':123}
 p.write_text(json.dumps(data));return p,data

def fake_attach(field):
 def attach(data,day,*,bars_loader):
  assert bars_loader('600000.SH')==[]
  data['enrichments']['600000.SH'][field]={'status':'ok','hit':False,'model':'test'}
  x={'coverage':{'requested':1,'ok':1,'unavailable':0},'status':'ok'};data[field+'_contract']=x;return x
 return attach

def test_dry_run_preserves_original(tmp_path,monkeypatch):
 m=load();p,data=fixture(tmp_path)
 monkeypatch.setattr(m,'ATTACHERS',{k:fake_attach(k) for k in m.FIELDS})
 result=m.recalculate([p],lambda code:[],apply=False)
 assert json.loads(p.read_text())==data
 assert result['files']==1

def test_apply_updates_only_signal_fields(tmp_path,monkeypatch):
 m=load();p,data=fixture(tmp_path)
 monkeypatch.setattr(m,'ATTACHERS',{k:fake_attach(k) for k in m.FIELDS})
 m.recalculate([p],lambda code:[],apply=True)
 new=json.loads(p.read_text());assert new['other_contract']==123
 assert new['enrichments']['600000.SH']['other']=={'preserve':True}
 assert all(new['enrichments']['600000.SH'][k]['model']=='test' for k in m.FIELDS)

def test_failure_before_apply_preserves_all_files(tmp_path,monkeypatch):
 m=load();p,data=fixture(tmp_path)
 def fail(*a,**k):raise RuntimeError('source failure')
 monkeypatch.setattr(m,'ATTACHERS',{'jinlong':fake_attach('jinlong'),'yinlong':fail,'beili':fake_attach('beili')})
 import pytest
 with pytest.raises(RuntimeError):m.recalculate([p],lambda code:[],apply=True)
 assert json.loads(p.read_text())==data

def test_apply_rolls_back_prior_file_after_write_failure(tmp_path,monkeypatch):
 m=load();p,data=fixture(tmp_path);q=tmp_path/'second.json';q.write_bytes(p.read_bytes());orig=q.read_bytes()
 monkeypatch.setattr(m,'ATTACHERS',{k:fake_attach(k) for k in m.FIELDS})
 actual=m.atomic_write;calls=[]
 def write(path,content):
  calls.append(path)
  if path==q:raise OSError('disk failure')
  return actual(path,content)
 monkeypatch.setattr(m,'atomic_write',write)
 import pytest
 with pytest.raises(OSError):m.recalculate([p,q],lambda code:[],apply=True)
 assert json.loads(p.read_text())==data
 assert q.read_bytes()==orig
 assert calls==[p,q,p]


def test_rollback_attempts_all_prior_files(tmp_path,monkeypatch):
 m=load();p,data=fixture(tmp_path);q=tmp_path/'second.json';q.write_bytes(p.read_bytes());r=tmp_path/'third.json';r.write_bytes(p.read_bytes())
 monkeypatch.setattr(m,'ATTACHERS',{k:fake_attach(k) for k in m.FIELDS})
 actual=m.atomic_write;calls=[]
 def write(path,content):
  calls.append(path)
  if path==r or (path==q and calls.count(q)==2):raise OSError('disk failure')
  return actual(path,content)
 monkeypatch.setattr(m,'atomic_write',write)
 import pytest
 with pytest.raises(RuntimeError,match='rollback failures'):
  m.recalculate([p,q,r],lambda code:[],apply=True)
 assert calls==[p,q,r,q,p]
 assert json.loads(p.read_text())==data

