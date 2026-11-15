#!/usr/bin/env python3
"""Recalculate only dragon overlays using read-only local bars; default dry-run.

Stage every candidate in memory before any writes. Individual file replacement is
atomic. On a write failure, restore previously replaced files from memory. For
operational rollback retain a separate verified backup before using --apply.
"""
from __future__ import annotations
import argparse,copy,json,os,pathlib,sqlite3,tempfile
try:
 import attach_low_chip_jinlong as gold
 import attach_low_chip_yinlong as silver
 import attach_low_chip_beili as structure
except ModuleNotFoundError:
 from scripts import attach_low_chip_jinlong as gold
 from scripts import attach_low_chip_yinlong as silver
 from scripts import attach_low_chip_beili as structure
ROOT=pathlib.Path(__file__).resolve().parents[1]
FIELDS=('jinlong','yinlong','beili')
ATTACHERS={'jinlong':gold.attach,'yinlong':silver.attach,'beili':structure.attach}

def atomic_write(path: pathlib.Path,content: bytes):
 mode=path.stat().st_mode & 0o777
 fd,name=tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
 try:
  with os.fdopen(fd,'wb') as out:
   out.write(content);out.flush();os.fsync(out.fileno())
  os.chmod(name,mode);os.replace(name,path)
 finally:
  pathlib.Path(name).unlink(missing_ok=True)

def recalculate(paths,bars_loader,*,apply=False):
 candidates=[];summary={'files':len(paths),'apply':apply,'counts':{k:{'requested':0,'ok':0,'unavailable':0,'hit':0} for k in FIELDS},'days':[]}
 for path in paths:
  raw=path.read_bytes();old=json.loads(raw);new=copy.deepcopy(old);day=new['data_as_of'];codes=new.get('intersection',[])
  for field in FIELDS:
   before=copy.deepcopy(new)
   contract=ATTACHERS[field](new,day,bars_loader=bars_loader)
   cov=contract['coverage']
   if cov['requested']!=len(codes) or cov['ok']+cov['unavailable']!=len(codes):raise RuntimeError('coverage mismatch '+day+' '+field)
   # Compare all keys after removing only the permitted field and contract.
   compare_before=copy.deepcopy(before);compare_after=copy.deepcopy(new)
   for obj in (compare_before,compare_after):
    obj.pop(field+'_contract',None)
    for entry in obj.get('enrichments',{}).values():entry.pop(field,None)
   if compare_before!=compare_after:raise RuntimeError('unrelated mutation '+day+' '+field)
   for k in ('requested','ok','unavailable'):summary['counts'][field][k]+=cov[k]
   summary['counts'][field]['hit']+=sum(bool(new['enrichments'][c][field].get('hit')) for c in codes)
  encoded=(json.dumps(new,ensure_ascii=False,separators=(',',':'))+'\n').encode()
  candidates.append((path,raw,encoded));summary['days'].append(day)
  print(day,'pool',len(codes),flush=True)
 if apply:
  written=[]
  try:
   for path,raw,new in candidates:
    if path.read_bytes()!=raw:raise RuntimeError('file changed during staging '+str(path))
    atomic_write(path,new);written.append((path,raw))
    if path.read_bytes()!=new:raise RuntimeError('write readback mismatch '+str(path))
  except BaseException as original:
   failures=[]
   for path,raw in reversed(written):
    try:
     atomic_write(path,raw)
     if path.read_bytes()!=raw:raise RuntimeError('restore readback mismatch')
    except BaseException as error:
     failures.append(str(path)+': '+str(error))
   if failures:raise RuntimeError('rollback failures: '+'; '.join(failures)) from original
   raise
 return summary

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');parser.add_argument('--day');args=parser.parse_args()
 paths=sorted((ROOT/'public/data/low-chip-history').glob('????-??-??.json'))+[ROOT/'public/data/a-low-chip-stocks.json']
 if args.day:paths=[p for p in paths if json.loads(p.read_text())['data_as_of']==args.day]
 db=sqlite3.connect('file:'+str(gold.CACHE_DB)+'?mode=ro',uri=True);cache={}
 def loader(code):
  bare=code.split('.')[0]
  if bare not in cache:cache[bare]=gold.load_cached(db,bare)
  return cache[bare]
 try:result=recalculate(paths,loader,apply=args.apply)
 finally:db.close()
 print(json.dumps(result,ensure_ascii=False),flush=True)
if __name__=='__main__':main()
