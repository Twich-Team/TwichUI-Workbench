"""Serve the actual built task UI with only synthetic, in-memory records."""

import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import mimetypes
from pathlib import Path
import secrets
from urllib.parse import urlsplit
from uuid import UUID

FILE = 'tests/test_sample_label.lua'
EXPECTED = 'A label with spaces around it displays as “Map”.'
SUMMARY = 'Add a test for spaces around a label'
DESCRIPTION = 'Make sure spaces around a label are removed before it is shown.'
SOURCE = '!!!TwichUI/sample_label.lua'
RESULT = 'sample-checked'
SAMPLE_DETAILS = '''<aside class="review-preview" aria-label="Offline owner review">
  <strong>Offline review · Simulated examples</strong>
  <p>Try a task description or answer the sample question. Inputs last until this preview closes.
  The approval buttons are for review only, and nothing runs or publishes.</p>
  <nav aria-label="Preview walkthrough"><a href="/">Work</a>
  <a href="/intake/new">Create a task</a><a href="/intake/sample-approval">Run permission</a>
  <a href="/intake/sample-progress">Progress</a><a href="/intake/sample-attention">Needs attention</a>
  <a href="/executions/sample-checked">Checked result</a></nav></aside>'''
PREVIEW_STYLE = '''
.review-preview {padding: .85rem 1.2rem; border-bottom: 1px solid #745435; background: #33251a; color:#f0ddbd;}
.review-preview p {margin:.4rem 0 .6rem; max-width:65ch;}
.review-preview nav {display:flex; flex-wrap:wrap; column-gap:1.3rem;}
.review-preview nav a {display:inline-flex; align-items:center; min-height:44px; text-decoration:underline;}
.primary-nav a:not([href="/"]), .workspace-examples, .intake-page .back-link[href^="/backlog"] {display:none;}
'''
PREVIEW_CONTROLS = '''
// Server-side routing is the authority boundary. Keep the visible approval
// controls disabled as React renders or refreshes the demonstration cards.
const selectors = '.run-approval button,.run-approval input,.proposal-decisions button,' +
  '.proposal-decisions input,.proposal-decisions textarea,.scope-form button,' +
  '.artifact-form button,.proposal-panel form button,#priority';
const sync = () => {
  for (const control of document.querySelectorAll(selectors)) {
    if (!control.disabled) control.disabled = true;
  }
};
new MutationObserver(sync).observe(document.getElementById('root'),
  {subtree:true,childList:true,attributes:true,attributeFilter:['disabled']});
sync();
'''


def now():
    return datetime.now(timezone.utc).isoformat()


def make_plan(identity, with_test):
    plan = dict(id=identity+'-plan', revision=1, binding='sample-only', scope_revision=1,
                basis='demo', model='Sample response', artifacts=[], required_artifacts=[],
                scope_conflicts=[], approval_blockers=[], research_only=False,
                content=dict(summary=SUMMARY if with_test else 'An example plan to review',
                             value='This example shows how Workbench explains the work before asking for a decision.',
                             delivery='implementation', visual='nonvisual',
                             behavior=['Keep the text inside the label.'], scope=[
                                 f'Add one check to {FILE}.' if with_test else 'Review the behavior described in your request.'],
                             exclusions=['Changes to addon behavior, installation and publishing.'],
                             acceptance=[EXPECTED] if with_test else ['Record what should happen and how to check it.'],
                             uncertainty=['This is a scripted example. It does not assess your request or run checks.'],
                             feasibility='Sample only.', dependencies=[], risks=[], evidence_ids=[],
                             stages=['Confirm the expected behavior.', 'Prepare and check the work.']))
    if with_test:
        plan['task_contract'] = dict(
            category='lua-regression-tests', acceptance=[EXPECTED], writable_paths=[FILE],
            repository='Example addon', base_ref='Sample source', resolved_base=None,
            pr_repository='Example destination', pr_base_branch='main', path_candidates=[],
            scope_missing=[], verification_commands=[dict(purpose='Check the sample test', argv=['lua', FILE], cwd='.')],
            independent_checks=['The check would have to catch an unchanged label.'],
            evidence=[], execution_blockers=[], resource_limits=dict(attempts=1),
            behavioral_evidence=dict(status='review_required', scenarios=[dict(
                id='sample-example', acceptance_index=0, args=[' Map '], target=['FormatLabel'],
                observe=dict(path=[], expected='Map'), incorrect=' Map ', fixture='module',
                method=False, setup=[], test_path=FILE, source_paths=[SOURCE])]))
    return plan


def make_request(identity, original, *, demo_test=False, stage='completed', question=None):
    plan = make_plan(identity, demo_test)
    task = dict(id=identity+'-work', kind='task', title=original['title'], scope_review_required=False,
                proposal_id=plan['id'])
    scoped = stage == 'tasks_ready'
    return dict(id=identity, status=stage, created_at=now(), revision=1, original=original,
                human_scope=dict(revision=1, visual_requirement=original['visual_requirement'],
                                 stage='implementation', origin='human'),
                scope_history=[], proposals=[plan], tasks=[task] if scoped else [],
                decisions=[], jobs=[], clarifications=[], evidence=[],
                result=dict(clarification=question) if question else None,
                priority=dict(reason='Sample priority', manual=False, priority=3))


class Demo:
    def __init__(self):
        self.session = secrets.token_urlsafe(24)
        self.samples = {}
        self.answers = {}
        for key, title, stage in (
            ('plan', 'Sample · Add a test for label spacing', 'completed'),
            ('approval', 'Sample · See what run permission covers', 'tasks_ready'),
            ('progress', 'Sample · Follow work through checking', 'tasks_ready'),
            ('attention', 'Sample · Clarify what should happen', 'waiting_for_information'),
            ('result', 'Sample · Label-spacing test result', 'tasks_ready'),
        ):
            identity = 'sample-'+key
            original = dict(kind='feature', title=title, description=DESCRIPTION,
                            visual_requirement='nonvisual', regression_tests=dict(acceptance=[EXPECTED]))
            self.samples[identity] = make_request(identity, original, demo_test=True, stage=stage,
                question='Should the label keep the spaces inside the text?' if key=='attention' else None)
        self.submitted = {}

    def view(self, route):
        parts = route.strip('/').split('/')
        if route == '/api/session':
            return dict(csrf_token=self.session)
        if route == '/api/supported-tasks':
            return dict(items=[])
        if route == '/api/tasks':
            return []
        if route == '/api/intake':
            items = [*reversed(self.submitted.values()), *self.samples.values()]
            return dict(items=items, total=len(items), page=1, planner='demo')
        if route == '/api/real-executions':
            return dict(available=True, total=1, page_size=12, items=[dict(
                id=RESULT, title=self.samples['sample-result']['original']['title'], status='checked',
                message='Illustration of a checked result.', verification='passed', has_candidate=True, created_at=now())])
        if route == '/api/real-executions/'+RESULT:
            return dict(id=RESULT, title=self.samples['sample-result']['original']['title'], status='checked',
                        current_grant=False, current_task=True, base='Sample source', grant_id='Sample only',
                        expires_at='Not applicable', workflow_task=dict(intake_id='sample-result', task_id='sample-result-work'),
                        message='Simulated result. No actual checks ran.', result=dict(inference_ran=False,
                        verification='passed', candidate_id='Sample only', files=[dict(path=FILE, kind='modified',
                            additions=1, deletions=0, diff='--- a/'+FILE+'\n+++ b/'+FILE+'\n@@ -1 +1,2 @@\n'
                            ' assert(FormatLabel("Map") == "Map")\n+assert(FormatLabel(" Map ") == "Map")\n')],
                        checks=[dict(status='pass', rc=0, command=command, out='Simulated pass for review.') for command in (
                            ['luac', '-p', FILE], ['lua', FILE], ['lua', 'sample/oracle.lua'], ['bash', 'tests/run.sh'])]))
        if route == '/api/real-executions/'+RESULT+'/delivery':
            return None
        if parts[:2] != ['api','intake'] or len(parts) < 3:
            raise KeyError
        identity = parts[2]
        item = self.samples.get(identity) or self.submitted[identity]
        if len(parts) == 3:
            return item
        if len(parts) != 6 or parts[3:5] != ['tasks',identity+'-work'] or item['status']!='tasks_ready':
            raise KeyError
        if parts[5]=='execution':
            stage, message = {
                'sample-approval': ('waiting', 'Review the example run permission below.'),
                'sample-progress': ('checking', 'In this example, the change is ready and Workbench is checking it.'),
                'sample-result': ('checked', 'The sample checks passed.'),
            }[identity]
            runs = [dict(id=RESULT, status='checked', verification='passed', current_task=True, candidate_id='sample')] if stage=='checked' else []
            return dict(stage=stage, message=message, runs=runs, current_approval=True)
        if parts[5]=='run-approval':
            if identity != 'sample-approval':
                return dict(available=False, offer=None, message='Historical sample. No approval was saved.')
            return dict(available=True, binding='sample-only', scope=item['proposals'][0],
                        message='Example permission only.', offer=dict(
                            id='sample-offer', fingerprint='sample-only', title=item['original']['title'],
                            paths=[FILE], repository='Example addon', base_branch='main', base='Sample source',
                            checks=[], limits=dict(attempts=1), budget=dict(activate_once=True, publication='hold',
                                turns=20, generated_tokens=24000, overall_seconds=5400, response_tokens=2500,
                                implementation_seconds=1200, complete_run_seconds=2400, publication_seconds=900)))
        raise KeyError

    def write(self, route, data):
        if route == '/api/intake/sample-attention/clarification':
            answer = data.get('text')
            if not isinstance(answer, str) or not answer.strip() or len(answer)>1600:
                raise ValueError('Enter a short reply.')
            item = self.samples['sample-attention']
            item['clarifications'] = [dict(text=answer, revision=1, created_at=now())]
            item.update(status='completed', result=None)
            return dict(saved=True)
        identity = str(UUID(data['request_id']))
        if identity in self.submitted:
            return self.submitted[identity]
        if len(self.submitted) >= 7:
            raise ValueError('This preview has room for seven practice requests.')
        title, description = data['title'], data['description']
        scope = data.get('visual_requirement', 'unclassified')
        if (not isinstance(title, str) or not title.strip() or len(title)>160 or
                not isinstance(description, str) or not description.strip() or len(description)>6000 or
                scope not in ('unclassified','visual','nonvisual')):
            raise ValueError('Enter a short title and description.')
        original = dict(title=title, description=description, visual_requirement=scope,
                        kind='issue' if data.get('kind')=='issue' else 'feature')
        item = make_request(identity, original, demo_test=bool(data.get('regression_tests')))
        self.submitted[identity] = item
        return item


def serve(port, assets):
    assets = assets.resolve(strict=True)
    selected = [p for directory in ('assets','art') for p in (assets/directory).rglob('*') if p.is_file()]
    if any(not p.resolve().is_relative_to(assets) for p in selected) or sum(p.stat().st_size for p in selected)>16<<20:
        raise ValueError('The preview needs a bounded, local set of built assets.')
    static = {'/'+p.relative_to(assets).as_posix(): (mimetypes.guess_type(p)[0] or 'application/octet-stream', p.read_bytes())
              for p in selected}
    static['/preview-controls.js'] = ('text/javascript', PREVIEW_CONTROLS.encode())
    index = (assets/'index.html').read_text().replace('</head>', '<style>'+PREVIEW_STYLE+'</style></head>').replace(
        '<div id="root">', SAMPLE_DETAILS+'<script defer src="/preview-controls.js"></script><div id="root">')
    demo = Demo()
    host = f'127.0.0.1:{port}'
    writable = {'/api/intake','/api/intake/sample-attention/clarification'}

    class Handler(BaseHTTPRequestHandler):
        timeout = 5

        def log_message(self, *_):
            pass

        def reply(self, status, body, kind='application/json'):
            if not isinstance(body, bytes):
                body = json.dumps(body).encode()
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy',
                             "default-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; form-action 'self'")
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(body)

        def dispatch(self):
            if self.headers.get('Host') != host:
                return self.reply(403, dict(detail='Open the preview at its loopback address.'))
            route = urlsplit(self.path).path
            if self.command == 'POST':
                if route not in writable:
                    return self.reply(405, dict(detail='This preview cannot save approvals or start work.'))
                if (self.headers.get('Origin') != 'http://'+host or
                        self.headers.get('X-CSRF-Token') != demo.session):
                    return self.reply(403, dict(detail='Send practice input from this preview page.'))
                try:
                    size = int(self.headers.get('Content-Length','0'))
                    if not 0 < size <= 32768:
                        raise ValueError('Practice input is too large.')
                    data = json.loads(self.rfile.read(size))
                    if not isinstance(data, dict):
                        raise ValueError('Use the practice form.')
                    return self.reply(200, demo.write(route, data))
                except (KeyError, TypeError, ValueError) as exc:
                    return self.reply(422, dict(detail=str(exc)[:180]))
            if self.command not in ('GET','HEAD'):
                return self.reply(405, dict(detail='This preview only accepts practice input.'))
            if route.startswith('/api/'):
                try:
                    value = demo.view(route)
                except KeyError:
                    return self.reply(404, dict(detail='Outside the task walkthrough.'))
                return self.reply(200, value)
            if route in static:
                kind, body = static[route]
                return self.reply(200, body, kind)
            if route in ('/','/intake','/executions') or route.startswith(('/intake/','/executions/')):
                return self.reply(200, index.encode(), 'text/html; charset=utf-8')
            return self.reply(404, dict(detail='Outside the task walkthrough.'))

        do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = dispatch

    with HTTPServer(('127.0.0.1', port), Handler) as server:
        server.serve_forever()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('assets', type=Path)
    parser.add_argument('--port', type=int, required=True)
    args = parser.parse_args()
    serve(args.port, args.assets)
