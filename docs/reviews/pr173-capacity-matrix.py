import hashlib, json, random, sys
from dataclasses import replace
sys.path.insert(0,sys.argv[1])
import review_domain as domain
import review_document as d
rng=random.Random(173)
findings=[domain.Finding(f'F{i}', f'Error {i}', 'High', domain.StatusOpen(), domain.AnchorLegacy(f'{i}.py',1)) for i in (1,2,3)]
base=d.ReviewDocument(domain.Snapshot(3,1,None,4,domain.PARTIAL,findings,0),d.ReviewedHeader('a'*40,'partial'),None,d.ReviewView('a'*40,'opencode-go',(),d.ScopeView(1,0,12,'full'),(),d.SafeProse('Prosa')),(),(),None,None)
outputs=[]
for index in range(48):
    def prose():return d.SafeProse(rng.choice(['x','ñ漢','texto '])*rng.choice([0,1,200,4000,8001,30000]))
    document=replace(base,review=replace(base.review,prose=prose()) if index%4 else None,
        historical=prose() if index%3 else None,
        explanations=tuple(d.ExplanationView(f'F{i}',prose()) for i in range(1,1+index%4)),
        failure=d.FailureView('b'*40,'worker-failed',prose()) if index%2 else None,
        notices=tuple(d.Notice('no-entendido',comment_id=i) for i in range(1,1+index%4)),
        covered=d.CoverageBook('a'*40,tuple((f'path{i}.py','c'*40) for i in range([0,3,200,401][index%4]))) if index%3 else None)
    budget=domain.StorageBudget(comment_max_bytes=[2000,5000,18000,60000][index%4],comment_max_chars=[3000,9000,60000][index%3])
    prepared=d.prepare_publication(document,budget)
    if isinstance(prepared,d.PreparedPublication):
        outputs.append((index,'prepared',hashlib.sha256(prepared.body.encode()).hexdigest(),hashlib.sha256(prepared.summary.encode()).hexdigest()))
    else:outputs.append((index,type(prepared).__name__,repr(prepared)))
print(json.dumps(outputs))
