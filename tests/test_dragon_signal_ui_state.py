import pathlib,re,subprocess,json
R=pathlib.Path(__file__).resolve().parents[1]
P=R/'src/pages/rolling/low-chip.astro'

def run_client(name,arg,ssr=False):
 text=P.read_text()
 prefix='function '+name+'(' if ssr else '      function '+name+'('
 start=text.index(prefix);end=text.index('\nfunction ' if ssr else '\n      function ',start+10)
 fn=text[start:end]
 if ssr:fn=re.sub(r'\(bl: any\): string','(bl)',fn)
 result=subprocess.run(['node','-e',fn+'\nconsole.log(JSON.stringify('+name+'('+json.dumps(arg)+')));'],capture_output=True,text=True,check=True)
 return json.loads(result.stdout)

def test_voided_label_ssr_client_agree():
 data={'status':'ok','today':True,'hit':False,'voided':True}
 assert run_client('beiliLabel',data,True)==run_client('beiliLabel',data)=='失效'

def test_voided_event_is_labelled_invalid_in_client():
 assert run_client('beiliLabel',{'status':'ok','today':True,'hit':False,'voided':True})=='失效'

def test_voided_title_precedes_today():
 v=run_client('beiliTitle',{'status':'ok','today':True,'hit':False,'voided':True,'last_hit_date':'2026-11-05','invalidated_at':'2026-11-10'})
 assert '失效' in v and '2026-11-10' in v

def test_ui_window_has_explicit_inclusive_distance():
 text=P.read_text()
 for label in ('金龙信号：距今至多 20 个交易日','银龙信号：距今至多 20 个交易日','底背离：距今至多 20 个交易日'):
  assert label in text
 assert '背景信号' in text

def test_separated_today_tooltip_uses_its_own_reference():
 data={'status':'ok','today':True,'hit':True,'separated_today':True,'direct_today':False,'cl1':8,'cl2':12,'cl3':10,'difl1':-0.4,'difl2':-0.2,'difl3':-0.8,'macd_hist':-0.1}
 value=run_client('beiliTitle',data)
 assert '前段 10' in value and '前段 -0.8' in value

