"""Local audit probes: assert the presence of TN4–TN7, not successful fixes."""
import json
import os
import sys
from dataclasses import replace
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

import yaml
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import subprocess
import tempfile
import review_coordinator as coordinator
import review_config as config
import review_document as doc
import review_domain as domain
import review_sticky as sticky

class Visible(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags=[]; self.text=[]
    def handle_starttag(self,tag,attrs):self.tags.append(tag)
    def handle_data(self,data):
        if data.strip():self.text.append(data)

def book_closer():
    book=doc.CoverageBook('a'*40,(('src/x--!><h1>Injected title</h1>.py','b'*40),))
    state=domain.Snapshot(3,1,None,1,domain.PARTIAL,[],0)
    document=doc.ReviewDocument(state,doc.ReviewedHeader('a'*40,'partial'),book,None,(),(),None,doc.SafeProse('Previous review'))
    prepared=doc.prepare_publication(document)
    assert isinstance(prepared,doc.PreparedPublication)
    assert isinstance(doc.read_document(prepared.body),doc.ReviewDocument)
    line=next(l for l in prepared.body.splitlines() if l.startswith(sticky.COVERED_PREFIX))
    parser=Visible();parser.feed(line)
    assert parser.tags==['h1'],(parser.tags,parser.text)
    assert 'Injected title' in parser.text
    return {'prepared_and_read_valid':True,'visible_tags':parser.tags,'visible_text':parser.text}

def action_identity():
    action=yaml.safe_load((ROOT / 'action.yml').read_text())
    values={}
    for step in action['runs']['steps']:
        run=step.get('run','')
        for operation in ('prepare','run','publish'):
            if f'review.py" {operation}' not in run:continue
            env={k:('anchors' if v=='${{ inputs.finding_identity }}' else str(v)) for k,v in step.get('env',{}).items()}
            with patch.dict(os.environ,env,clear=True):
                values[operation]=config.politica_de_revision().finding_identity
    assert values=={'prepare':'titles','run':'anchors','publish':'titles'},values
    return values

def malformed_anchor():
    entrada={'file':'a.py','line':1,'title':'Bug','severity':'High','cause_hint':42}
    texto='<!-- ai-review:findings='+json.dumps({'findings':[entrada]})+' -->\nCOVERAGE: complete'
    parsed=domain.parse_model_findings(texto,conservar_anclas=True)
    assert sticky.modelo_utilizable(parsed)
    observation=domain.observation_de_entrada(parsed['findings'][0])
    report=domain.validar_reporte([observation],domain.COMPLETE_CLAIM,domain.RepositoryFacts())
    state=domain.Snapshot(3,1,None,1,domain.UNKNOWN,[],0)
    decision=domain.accept_report(state,domain.ReviewPlan(policy=domain.ReviewPolicy(finding_identity='anchors')),report)
    try:domain.encode_snapshot(decision.snapshot)
    except ValueError as error:return {'model_ok':True,'publication_error':str(error)}
    raise AssertionError('Expected malformed cause_hint to fail encoding')

def unpaged_recovery():
    first_page=[{'id':1000+i,'run_attempt':1,'display_title':f'PR 999 solicitud {i+1}'} for i in range(100)]
    old={'id':900,'run_attempt':1,'display_title':'PR 123 solicitud 1','status':'in_progress'}
    request=domain.WorkRequest(1,'review',origin='push',target={'head_sha':'a'*40,'base_sha':'b'*40,'policy_digest':'p'})
    state=domain.Snapshot(3,1,None,1,domain.UNKNOWN,[],0,pending_requests=[request],request_count=1)
    calls=[]
    def fake_sh(*args,**kwargs):
        calls.append(args)
        data=first_page+[old] if '--paginate' in args else first_page
        return subprocess.CompletedProcess(args,0,json.dumps({'workflow_runs':data}),'')
    with patch.object(coordinator.review_github,'sh',side_effect=fake_sh), tempfile.TemporaryDirectory() as work:
        deliveries,missing,rejected=coordinator.cosechar(state,'o/r',123,'main',work,None)
    assert missing==[request] and not deliveries and not rejected
    document=replace(doc.read_document(''),memory=state)
    plan=coordinator.decidir(state,state,(),(),(),missing,None,facts=domain.RepositoryFacts(revision=domain.Revision('b'*40,'a'*40,'p')),policy=domain.ReviewPolicy(),admisible=True,hay_sticky=True,permisos=lambda _: 'write',ultimo_comentario=lambda:0,document=document)
    assert plan.trabajo==(request,)
    return {'existing_worker_on_page_two':old['id'],'api_calls':len(calls),'queries':[list(x) for x in calls],'request_scheduled_again':plan.trabajo[0].id}

print(json.dumps({'coverage_html_closer':book_closer(),'action_identity':action_identity(),'anchor_boundary':malformed_anchor(),'unpaged_recovery':unpaged_recovery()},ensure_ascii=False,indent=2))
