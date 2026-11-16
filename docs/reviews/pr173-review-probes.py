"""Read-only review probes; all GitHub effects are simulated."""
import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

root = Path(sys.argv[1]).resolve()
sys.path[:0] = [str(root), str(root / 'tests')]
import review
import review_domain as domain
import review_document as doc
import review_coordinator as coord
import review_github as github
import test_coordinador_convergente as flow
from test_review_document import document


def historical_poison():
    outcomes = {}
    for legacy in (False, True):
        fake = flow.GitHubFalso()
        flow.correr(fake, 'pull_request_target')
        fake.worker(1, flow.resultado(1, flow.A, ['hallazgo nuevo']))
        if legacy:
            old = dict(fake.corridas[0], id=1, display_title='155', status='completed')
            fake.corridas.append(old)
        before = fake.sticky()
        codes = []
        for _ in range(2):
            code, dispatches, stderr = flow.correr(fake, 'workflow_dispatch', aviso=(900, 1))
            codes.append(code)
            assert not dispatches
        outcomes['with_legacy' if legacy else 'current_only'] = {
            'exit_codes': codes,
            'pending': [r.id for r in fake.estado().pending_requests],
            'sticky_unchanged': fake.sticky() == before,
            'message': stderr.strip(),
        }
    assert outcomes['current_only']['pending'] == []
    assert outcomes['with_legacy']['pending'] == [1]
    assert outcomes['with_legacy']['sticky_unchanged']
    return outcomes


class Comments:
    def __init__(self, body):
        self.body = body
        self.writes = 0
    def leer(self):
        return [{'id': 1, 'user': 'github-actions[bot]', 'body': self.body}]
    def parchar(self, cid, body):
        self.body = body
        self.writes += 1


def capacity_paths():
    previous = document()
    previous_body = doc.prepare_publication(previous).body
    results = {}
    for case in ('initial', 'redacted', 'body'):
        secret = 'TOKEN123'
        title = ('x' * 2000) if case == 'initial' else (secret * 100 if case == 'redacted' else 'nuevo')
        finding = domain.Finding('F1', title, 'High', domain.StatusOpen(), domain.AnchorLegacy('a.py', 1))
        state = replace(previous.memory, generation=2, findings=[finding], next_id=2, completion=domain.COMPLETE_CLAIM)
        raw_size = domain.encode_snapshot(state).bytes
        budget = domain.StorageBudget(max_bytes=800 if case == 'initial' else raw_size + 10)
        if case == 'body':
            budget.comment_max_bytes = len(previous_body.encode()) + 20
            budget.comment_max_chars = budget.comment_max_bytes
        accepted = review.DirectAccepted(state, ('F1',), True)
        comments = Comments(previous_body)
        code = 0
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            (work / 'result.json').write_text(json.dumps({'result': 'Revisión\n<!-- ai-review:findings={"findings":[]} -->\nCOVERAGE: complete'}))
            (work / 'manifest.json').write_text(json.dumps({'head': 'b'*40, 'reviewed':['a.py'], 'mode':'full'}))
            with patch.dict(os.environ, {'REPO':'o/r','PR_NUMBER':'1','HEAD_SHA':'b'*40,'API_KEY':secret if case=='redacted' else ''}, clear=True), \
                 patch.object(review,'build_findings',return_value=accepted), \
                 patch.object(review,'libro_actualizado',return_value=None), \
                 patch.object(review,'_repo_actual',return_value=None), \
                 patch.object(domain,'StorageBudget',return_value=budget), \
                 patch.object(github,'fetch_all_comments',side_effect=lambda *_: comments.leer()), \
                 patch.object(coord,'ComentariosGh',return_value=comments), \
                 patch.object(github,'informar_revision') as reported, \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                try:
                    review.cmd_publish(argparse.Namespace(work=str(work)))
                except SystemExit as error:
                    code = error.code
        persisted = doc.read_document(comments.body)
        assert isinstance(persisted, doc.ReviewDocument)
        assert persisted.memory == previous.memory
        assert persisted.reviewed == previous.reviewed
        assert reported.call_args.args == (False,)
        assert code == (0 if case == 'initial' else 1), (case, code)
        results[case] = {'exit_code':code, 'reviewed':False, 'previous_memory_preserved':True,'previous_header_preserved':True,'writes':comments.writes,'failure':persisted.failure.code if persisted.failure else None,'notices':[n.kind for n in persisted.notices]}
    return results

print(json.dumps({'history':historical_poison(), 'capacity':capacity_paths()}, ensure_ascii=False, indent=2))
