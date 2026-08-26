
from __future__ import annotations
import os,re,json,math,time,unicodedata
from datetime import datetime,date,timedelta
from zoneinfo import ZoneInfo
import requests, psycopg

from src.shared import (
    CAMPAIGN_DETAILS_TABLE,
    CAMPAIGN_RUNS_TABLE,
    DAILY_CHECK_TABLE,
    DATABASE_URL,
    get_secret,
    init_schema,
    kv_get,
    kv_set,
)
SPACE_ID="90175790679"
LIST_BASE1="901715970646"; LIST_BASE2="901716142212"; LIST_MEMBERS="901713986437"
FIELD_IDS={"whatsapp":"5ceea458-2f40-46dd-aed7-b4ee7d906f74","telefone":"87931c2e-7eed-4edd-84e2-f6da07af85e8","nome":"a6711d2a-f18f-4c4f-8e95-222821c3d3cf","origem":"c3286572-855f-495b-bfed-9444eaa6d985","bus":"7173fca5-64fa-4bac-b562-feab85043363"}
SELLER_IDS={"Tamara":"101213990","Márcio":"101264806"}

def norm(v):
    s=''.join(c for c in unicodedata.normalize('NFD',str(v or '')) if unicodedata.category(c)!='Mn')
    return re.sub(r'\s+',' ',s).strip().lower()
def phone(v):
    d=re.sub(r'\D','',str(v or ''))
    if len(d) in (10,11): d='55'+d
    return d if 12<=len(d)<=15 else ''
def dec(f):
    v=f.get('value')
    if v is None:return ''
    if f.get('type')=='labels' and isinstance(v,list):
        opts=(f.get('type_config') or {}).get('options') or []; by={str(o.get('id')):o for o in opts}
        return [str((by.get(str(x)) or {}).get('label') or (by.get(str(x)) or {}).get('name') or x) for x in v]
    return v
def fbyid(t,i):
    f=next((x for x in t.get('custom_fields',[]) if x.get('id')==i),None)
    return dec(f) if f else ''
def find(t,names):
    for f in t.get('custom_fields',[]):
        nn=norm(f.get('name'))
        if any(norm(a)==nn or norm(a) in nn for a in names): return dec(f)
    return ''
def has_tag(t,tag): return norm(tag) in {norm(x.get('name','') if isinstance(x,dict) else x) for x in t.get('tags',[])}

class C:
    def __init__(self,tok):
        self.s=requests.Session();self.s.headers.update({'Authorization':tok,'Content-Type':'application/json'});self.b='https://api.clickup.com/api/v2'
    def r(self,m,p,j=None,params=None):
        x=self.s.request(m,self.b+p,json=j,params=params,timeout=45)
        if not 200<=x.status_code<300: raise RuntimeError(f'ClickUp {x.status_code}: {x.text}')
        return x.json() if x.text.strip() else {}
    def tasks(self,lid,statuses=None):
        out=[];seen=set()
        for page in range(100):
            params=[('include_closed','true'),('page',page)]+[('statuses[]',x) for x in (statuses or [])]
            r=self.r('GET',f'/list/{lid}/task',params=params); ts=r.get('tasks',[])
            if not ts: break
            for t in ts:
                if t['id'] not in seen: seen.add(t['id']);out.append(t)
            if r.get('last_page') is True or len(ts)<100:break
        return out
    def upd(self,id,status): self.r('PUT',f'/task/{id}',{'status':status})
    def comment(self,id,text): self.r('POST',f'/task/{id}/comment',{'comment_text':text,'notify_all':False})
    def addlist(self,id,lid):
        try:self.r('POST',f'/list/{lid}/task/{id}',{})
        except Exception as e:
            if not re.search(r'already|exists|same list',str(e),re.I): raise
    def field(self,id,fid,val): self.r('POST',f'/task/{id}/field/{fid}',{'value':val})
    def tag(self,id,tag):
        try:self.r('POST',f'/task/{id}/tag/{requests.utils.quote(tag,safe="")}',{})
        except Exception:
            try:self.r('POST',f'/space/{SPACE_ID}/tag',{'tag':{'name':tag}})
            except Exception:pass
            self.r('POST',f'/task/{id}/tag/{requests.utils.quote(tag,safe="")}',{})
    def assignee(self,id,uid): self.r('PUT',f'/task/{id}',{'add_assignees':[int(uid)]})


def token():
    return get_secret("clickup_token")

def days(a,b):
    n=0;d=a
    while d<=b:
        if d.weekday() in (0,2,4):n+=1
        d+=timedelta(days=1)
    return n
def setlabel(c,t,fid,label,append):
    f=next((x for x in t.get('custom_fields',[]) if x.get('id')==fid),None)
    if not f: raise RuntimeError('Campo não encontrado: '+fid)
    opts=(f.get('type_config') or {}).get('options') or []
    o=next((o for o in opts if norm(o.get('label') or o.get('name'))==norm(label)),None)
    if not o: raise RuntimeError('Opção de label não encontrada: '+label)
    vals=list(f.get('value') or []) if append and isinstance(f.get('value'),list) else []
    if o.get('id') not in vals:vals.append(o.get('id'))
    c.field(t['id'],fid,vals)
def csv_text(rows):
    import csv,io
    b=io.StringIO();w=csv.writer(b,lineterminator='\r\n');w.writerow(['PHONE','nomeDoViajante','nomeDoVendedor']);w.writerows(rows);return b.getvalue()

init_schema()
cfg=json.loads(kv_get('automation_config') or '{}')
if not cfg.get('enabled'):
    print('Automação desabilitada. Nada a fazer.');raise SystemExit(0)
tok=token()
if not tok: print('Token automático ausente.');raise SystemExit(2)
c=C(tok)
campaign=cfg.get('campaign_tag','[MKT] CLUBE BEN BRINDE'); total=int(cfg.get('total',249))
end=date.fromisoformat(cfg.get('end_date','2026-08-31')); statuses=cfg.get('source_statuses',['apto para wpp','apto para wpp + e-mail'])
all_tasks=[]
for lid in cfg.get('source_lists',[LIST_BASE1,LIST_BASE2]):
    for t in c.tasks(lid,statuses):
        ph=phone(fbyid(t,FIELD_IDS['whatsapp']) or fbyid(t,FIELD_IDS['telefone']))
        if ph: all_tasks.append((lid,t,ph))
# dedupe phone; Base1 priority
by={}
for lid,t,ph in all_tasks:
    if ph not in by or (by[ph][0]!=LIST_BASE1 and lid==LIST_BASE1): by[ph]=(lid,t)
tagged=sum(1 for _,t in by.values() if has_tag(t,campaign))
remaining=max(0,total-tagged); dleft=days(date.today(),end); wanted=math.ceil(remaining/dleft) if dleft else 0
eligible=[(ph,lid,t) for ph,(lid,t) in by.items() if not has_tag(t,campaign)]
eligible=eligible[:wanted]
start=int(kv_get('seller_start:'+campaign) or 0)%2
sellers=cfg.get('sellers',['Tamara','Márcio'])
details=[]; rows=[]; errors=[]; ok=0
for i,(ph,lid,t) in enumerate(eligible):
    seller=sellers[(start+i)%len(sellers)]; name=str(fbyid(t,FIELD_IDS['nome']) or t.get('name','')).strip();rows.append([ph,name,seller])
    try:
        c.upd(t['id'],cfg.get('target_status','em fluxo'))
        tz=ZoneInfo(cfg.get('timezone','America/Sao_Paulo'))
        c.comment(t['id'],f"Disparo dia {datetime.now(tz).strftime('%d/%m/%Y')} às {cfg.get('dispatch_hour','10:00')}")
        c.addlist(t['id'],cfg.get('target_list',LIST_MEMBERS))
        setlabel(c,t,FIELD_IDS['origem'],cfg.get('target_bu','CLUBE DE FÉRIAS INTERMARES'),False)
        setlabel(c,t,FIELD_IDS['bus'],cfg.get('target_bu','CLUBE DE FÉRIAS INTERMARES'),True)
        c.tag(t['id'],campaign)
        if cfg.get('assign_clickup') and seller in SELLER_IDS:c.assignee(t['id'],SELLER_IDS[seller])
        ok+=1;details.append((t['id'],name,ph,seller,'CLICKUP','SUCESSO','Mesmo card adicionado ao Programa de Membros em fluxo'))
    except Exception as e:
        errors.append(f"{t['id']}: {e}");details.append((t['id'],name,ph,seller,'CLICKUP','ERRO',str(e)))
if len(rows)%2:kv_set('seller_start:'+campaign,(start+1)%2)
csvv=csv_text(rows)
with psycopg.connect(DATABASE_URL) as con:
    with con.cursor() as cur:
        cur.execute(f"""INSERT INTO {CAMPAIGN_RUNS_TABLE}(run_date,campaign,mode,requested,selected,csv_rows,cards_ok,errors,csv_status,asksuite_status,message,csv_text)
        VALUES(%s,%s,'LIVE',%s,%s,%s,%s,%s,'SUCESSO','PENDENTE - ENVIO MANUAL',%s,%s) RETURNING id""",
        (date.today(),campaign,wanted,len(rows),len(rows),ok,len(errors),'Execução automática seg/qua/sex',csvv));rid=cur.fetchone()[0]
        for x in details:
            cur.execute(f"INSERT INTO {CAMPAIGN_DETAILS_TABLE}(run_id,task_id,lead_name,phone,seller,stage,result,message)VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",(rid,*x))
        cur.execute(f"""INSERT INTO {DAILY_CHECK_TABLE}(check_date,campaign,mode,csv_rows,cards_updated,errors,asksuite_status,last_update,observation)
        VALUES(%s,%s,'LIVE',%s,%s,%s,'PENDENTE - ENVIO MANUAL',NOW(),%s)
        ON CONFLICT(check_date,campaign) DO UPDATE SET mode='LIVE',csv_rows=EXCLUDED.csv_rows,cards_updated=EXCLUDED.cards_updated,errors=EXCLUDED.errors,asksuite_status=EXCLUDED.asksuite_status,last_update=NOW(),observation=EXCLUDED.observation""",
        (date.today(),campaign,len(rows),ok,len(errors),'Execução automática'))
    con.commit()
print(json.dumps({'campaign':campaign,'wanted':wanted,'rows':len(rows),'ok':ok,'errors':errors},ensure_ascii=False))
