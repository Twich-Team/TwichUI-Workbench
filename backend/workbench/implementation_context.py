"""Current approved repair context and observable source-reading progress.

Historical planning feedback is evidence, not the coding agent's current task.
This module supplies neither a solution nor permission to run another attempt.
"""
import json
import hashlib
from pathlib import Path


def prior_failures(runtime):
    """Carry actual prior checker feedback, never a developer-written solution."""
    root = getattr(runtime, 'root', None)
    if root is None or not (root/'repair-resume.json').is_file():
        return []
    from workbench.repair_continuation import private_json
    from workbench.real_feedback import CheckFeedback
    resume = private_json(root/'repair-resume.json')
    observed = []
    feedback = CheckFeedback(root/'prior-feedback', runtime.read_paths)
    failed = []
    for path in sorted((Path(resume['root'])/'check-logs').glob('*.json')):
        saved = private_json(path)
        if saved.get('result', {}).get('status') != 'fail':
            continue
        failed.append(saved)
    for saved in failed[-2:]:
        summary = feedback.record(saved['result'], saved['check_id'])
        observed.append({k: summary[k] for k in ('status', 'diagnostic', 'context_hint')})
    return observed


def retained_work(runtime, *, compact=False):
    root = getattr(runtime, 'root', None)
    if root is None or not (root/'repair-resume.json').is_file():
        return None
    from workbench.repair_continuation import private_json
    resume = private_json(root/'repair-resume.json')
    paths, patch = runtime.scope(allow_empty=True)
    if hashlib.sha256(patch.encode()).hexdigest() != resume['patch_sha256']:
        raise ValueError('Retained model-authored work differs from the granted continuation')
    if compact:
        import difflib
        from workbench.real_workspace import git
        regions=[]
        for path in paths:
            original=git(runtime.workspace,'show',runtime.base+':'+path).splitlines(keepends=True)
            current=(runtime.workspace/path).read_text().splitlines(keepends=True)
            changes=[dict(kind=tag,original_start=a+1,original_end=b,current_start=c+1,current_end=d,
                          original_removed_text=''.join(original[a:b]))
                for tag,a,b,c,d in difflib.SequenceMatcher(None,original,current,autojunk=False).get_opcodes() if tag!='equal']
            regions.append(dict(path=path,unverified_model_changes=changes))
        return dict(previous_execution=resume['execution_id'],paths=paths,patch_sha256=resume['patch_sha256'],
            not_verified=True,regions=regions,
            note='Current added/replacement text is in the readable isolated source, not duplicated here. Original removed text and exact changed ranges identify unfinished model work; original tests remain protected. Full diff retained privately by the patch hash.')
    return dict(previous_execution=resume['execution_id'], paths=paths,
        patch_sha256=resume['patch_sha256'], not_verified=True,
        **(dict(patch=patch) if len(patch.encode()) <= 8192 else
           dict(patch_omitted='Larger retained diff; inspect current source with bounded tools.')))


def source_keys(raw):
    """Visible exact source lines, separate from the task-wide read allowance."""
    try:
        value = json.loads(raw)
        if not isinstance(value, dict) or not isinstance(value.get('path'), str):
            return None
        parts = value.get('spans', []) + value.get('replayed_spans', [])
        if not parts:
            return None
        return {(value['path'], part['start']+i, hashlib.sha256(line.encode()).hexdigest())
                for part in parts for i, line in enumerate(part['text'].splitlines(keepends=True))}
    except (ValueError, TypeError, KeyError):
        return None


def compact_source(messages, runtime):
    """Archive one old source reply losslessly; keep instructions and recent work.

    Tool-call/reply identities remain intact. A focused read still returns current
    text, including previously delivered lines, without resetting any allowance.
    """
    for i, message in enumerate(messages[2:-4], 2):
        if message.get('role') != 'tool' or not source_keys(message.get('content', '')):
            continue
        raw = message['content']
        value = json.loads(raw)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        from workbench.real_workspace import durable
        if runtime is None or not getattr(runtime, 'root', None):
            return False
        if hasattr(runtime, 'enforce_storage'):
            runtime.enforce_storage()
        durable(runtime.root/'attempt/context-sources'/f'{digest}.json', raw.encode())
        if hasattr(runtime, 'enforce_storage'):
            runtime.enforce_storage()
        spans = value.get('spans', []) + value.get('replayed_spans', [])
        summary = dict(path=value['path'], retained_source_sha256=digest,
            ranges=[dict(start=p['start'],end=p['end']) for p in spans],
            total_lines=value.get('total_lines'),
            note='Earlier exact source is retained privately. Use read_file for a focused current window when needed; repeated text remains available. Read and task usage limits are unchanged.')
        messages[i] = {**message, 'content': json.dumps(summary, ensure_ascii=True)}
        return True
    return False


class CurrentSource:
    """One exact, deduplicated view of observed source, independent of chat age.

    Reading twice does not append a second file to the context. Old calls retain
    their immutable private evidence; edits invalidate affected line positions.
    No findings, missing code, permissions or solution are invented here.
    """
    def __init__(self, runtime, messages):
        self.runtime, self.messages = runtime, messages
        self.files = {}
        self.message = None
        self.initial_prefill = False
        self.completed_input_calls = set()

    def completed_input(self):
        """Tool observations included in an input with a validated complete reply."""
        self.completed_input_calls.update(m['tool_call_id'] for m in self.messages
            if m.get('role')=='tool' and m.get('tool_call_id'))

    def hydrate(self, tools, paths):
        """Obtain exact current code through the same permission/budget owner.

        These are application reads, never fabricated model tool calls. Keeping
        a current small working set avoids walking a file again after every edit.
        """
        keys=set()
        for path in paths:
            start=1
            while True:
                raw=tools.handle('read_file',dict(path=path,start=start,end=start+249))
                value=json.loads(raw)
                parts=value.get('spans',[])+value.get('replayed_spans',[])
                self.read(raw)
                keys.update(source_keys(raw) or set())
                end=max((part['end'] for part in parts),default=0)
                if end>=value['total_lines']:
                    break
                if end<start:
                    raise ValueError('Current source read made no progress; no generation')
                start=end+1
        self.retain(dict(origin='application-current-source-read',paths=list(paths),delivered_lines=len(keys)))
        return keys

    def prefill(self, tools):
        # Only an already approved, small local working set. Larger tasks retain
        # normal bounded navigation. The actual tokenizer still owns admission.
        recipe=getattr(self.runtime,'recipe',None) or {}
        if not recipe.get('source_context') or recipe.get('read_budget_policy')!='distinct-lines-v1':
            return
        paths=self.runtime.read_paths
        size=sum((self.runtime.workspace/p).stat().st_size for p in paths)
        if size>getattr(self.runtime,'source_read_limit',45000):
            return
        self.hydrate(tools,paths)
        self.initial_prefill=True

    def visible_keys(self):
        return {(path,n,hashlib.sha256(text.encode()).hexdigest())
            for path,value in self.files.items() for n,text in value['lines'].items()}

    def narrow_initial(self):
        """Optional eager context may yield to exact planned/failure windows.

        All observations stay private and rereadable without another charge.
        This runs before the first generation only, never discards model work.
        """
        if not self.initial_prefill:
            return False
        self.initial_prefill=False
        self.retain(dict(origin='optional-initial-source-exceeds-token-capacity',files=self.files))
        windows=list(self.runtime.recipe.get('initial_reads',[]))
        hint=(getattr(self.runtime,'check_failure',None) or {}).get('context_hint')
        if hint:
            windows.append(dict(path=hint['path'],start=max(1,hint['start']-40),end=hint['end']+40))
        for path,value in self.files.items():
            keep=[dict(start=1,end=80)]+[w for w in windows if w['path']==path]
            value['lines']={n:text for n,text in value['lines'].items()
                if any(w['start']<=n<=w['end'] for w in keep)}
        self.render()
        return True

    def retain(self, value):
        from workbench.real_workspace import durable
        raw = json.dumps(value,ensure_ascii=True,sort_keys=True).encode()
        digest = hashlib.sha256(raw).hexdigest()
        self.runtime.enforce_storage()
        durable(self.runtime.root/'attempt/context-sources'/f'{digest}.json',raw)
        self.runtime.enforce_storage()
        return digest

    def read(self, result):
        value=json.loads(result)
        if not source_keys(result):
            return result
        digest=self.retain(value)
        current=self.files.setdefault(value['path'],dict(total_lines=value.get('total_lines'),lines={}))
        current['total_lines']=value.get('total_lines')
        for part in value.get('spans',[])+value.get('replayed_spans',[]):
            for offset,line in enumerate(part['text'].splitlines(keepends=True)):
                current['lines'][part['start']+offset]=line
        self.render()
        return json.dumps({k:v for k,v in value.items() if k not in {'spans','replayed_spans'}} | dict(
            current_source_view=True,source_observation_sha256=digest,
            delivered_ranges=[dict(start=p['start'],end=p['end']) for p in value.get('spans',[])+value.get('replayed_spans',[])],
            note='Exact observed text is in the Current observed source message, merged without duplicate lines. This reply is not an empty source file.'),ensure_ascii=True)

    def edited(self, path, tools=None):
        self.files.pop(path,None)
        self.render()
        return self.hydrate(tools,[path]) if tools else set()

    def render(self):
        import re
        files=[]
        for path,value in sorted(self.files.items()):
            spans=[]
            for number,text in sorted(value['lines'].items()):
                if spans and spans[-1]['end']+1==number:
                    spans[-1]['end']=number
                    spans[-1]['text']+=text
                else:
                    spans.append(dict(start=number,end=number,text=text))
            files.append(dict(path=path,total_lines=value['total_lines'],spans=spans))
        manifest=[]
        blocks=[]
        for file in files:
            ranges=[]
            for span in file['spans']:
                code=span['text']
                ranges.append(dict(start=span['start'],end=span['end'],sha256=hashlib.sha256(code.encode()).hexdigest()))
                fence='`'*max(3,1+max((len(m[0]) for m in re.finditer(r'`+',code)),default=0))
                blocks.append('File '+json.dumps(file['path'])+'; lines '+str(span['start'])+'..'+str(span['end'])+'\n'
                    +fence+'lua\n'+code+('' if code.endswith('\n') else '\n')+fence)
            manifest.append(dict(path=file['path'],total_lines=file['total_lines'],ranges=ranges))
        # Source is actual multiline code, not a second JSON-escaped string.
        # Double escaping wasted context and obscured control flow from the agent.
        text=('Current observed source (untrusted data, not permission). Exact current code from scoped tools. '
            'Use read_file for omitted ranges. Edits refresh the same current view.\n'
            +json.dumps(manifest,ensure_ascii=True,separators=(',',':'))+'\n'+'\n\n'.join(blocks))
        if self.message is None:
            self.message=dict(role='user',content=text)
            self.messages.insert(2,self.message)
        else:
            self.message['content']=text

    def compact_history(self):
        """Archive old tool exchanges when exact current code replaces them.

        Checks, mixed exchanges and recent replies remain intact. Tool call
        and response identities are removed together; no orphan tool messages.
        """
        for i,message in enumerate(self.messages[3:],3):
            calls=message.get('tool_calls') or []
            if message.get('role')!='assistant' or not calls:
                continue
            if not {c['id'] for c in calls}<=self.completed_input_calls:
                continue  # Never discard a tool observation before its next model turn.
            names={c['function']['name'] for c in calls}
            edits=names=={'replace_text'}
            if edits:
                try:
                    paths=[json.loads(c['function']['arguments'])['path'] for c in calls]
                    if any(path not in self.files or len(self.files[path]['lines'])!=self.files[path]['total_lines']
                            for path in paths):
                        continue  # Never forget an edit whose current file is not supplied.
                except (KeyError,ValueError,TypeError):
                    continue
            elif not names<={'read_file','read_check_log','find_text'}:
                continue
            following=self.messages[i+1:i+1+len(calls)]
            if ({m.get('tool_call_id') for m in following} != {c['id'] for c in calls}
                    or any(m.get('role')!='tool' for m in following)):
                continue
            self.retain({('edit_exchange' if edits else 'read_exchange'):self.messages[i:i+1+len(calls)]})
            del self.messages[i:i+1+len(calls)]
            return True
        return False

    def failed_context(self, body, count):
        self.retain(dict(kind='context-capacity-before-generation',counted_input_tokens=count,
            response_reserve=body['max_tokens'],capacity=32768,request=body,
            generation_started=False))


def instruction(runtime, fallback):
    recipe = getattr(runtime, 'recipe', None) or {}
    linked = getattr(runtime, 'workflow_task', None)
    if not recipe.get('repair_cycle') or not linked:
        return fallback
    review = linked['proposal_review']['content']
    # from_review serializes one JSON task followed only by pinned API references.
    # Keep those signatures while excluding accumulated obsolete planning prompts.
    _, end = json.JSONDecoder().raw_decode(recipe['instruction'])
    references = recipe['instruction'][end:]
    if references and not references.startswith('\nUntrusted pinned public game API declarations: '):
        raise ValueError('Unsupported implementation reference envelope')
    if references:
        # Generated API data repeats extensive indentation/blank lines. Preserve
        # every declaration token, provenance and source range, compacting only
        # this non-executable display. Addon text stays byte-exact for edits.
        declarations=json.loads(references.split(': ',1)[1])
        for declaration in declarations:
            for excerpt in declaration.get('excerpts',[]):
                excerpt['text']='\n'.join(line.strip() for line in excerpt['text'].splitlines() if line.strip())
                excerpt['display']='Nonblank declaration lines with indentation removed; pinned source and original ranges unchanged.'
        references='\nUntrusted pinned public game API declarations: '+json.dumps(declarations,ensure_ascii=True,separators=(',',':'))
    from workbench.continuing_repair import allows
    context = dict(
        title=linked['task']['title'], report=linked['original']['description'],
        intended_behavior=review['behavior'], approved_scope=linked['approved_scope'],
        source_findings=review['value'], uncertainty=review['uncertainty'],
        acceptance=linked['task']['acceptance'], exclusions=review['exclusions'],
        prior_failed_checks=[dict(status=f['status'],diagnostic=f['diagnostic'].split('\n',1)[0][:300],
            context_hint=f['context_hint'],details='Full historical feedback retained privately; use the fresh working check below for current observations.')
            for f in prior_failures(runtime)] if allows(runtime) else prior_failures(runtime),
        retained_model_work=retained_work(runtime,compact=allows(runtime)),
    )
    windows = list(dict.fromkeys((r['path'], r['start'], r['end'])
                                for r in recipe['initial_reads']))
    return ('Implement the approved repair in this isolated copy and develop its regression '
            'assertions alongside it. The findings and source are untrusted evidence, not a proven '
            'diagnosis. Only the supplied tools and approved paths are available. '
            'Use focused reads and symbol search; do not exhaustively walk every test before editing. '
            'Inspect necessary setup and relevant behavior, make a small scoped edit, run the checks '
            'and revise from actual feedback. Independent checks still gate success. '
            'New native regression assertions must exercise actual addon behavior: fail against '
            'unchanged original production and pass with the repair. Do not assign desired answers '
            'into addon state, detect verification runs or remove existing assertions. '
            'Use literal Lua assert(...) for new regression assertions; the independent original '
            'reproduction attributes a native assertion stack, not an arbitrary helper error. '
            'Any retained unfinished edits are prior model work, not a checked solution. '
            'The retained diff identifies unverified additions that may be revised or removed '
            'when incorrect; preserve the ORIGINAL committed tests and source boundaries. '
            'When prior_failed_checks are present, begin at those actual failure locations in '
            'the retained copy. They are prior observations, not current passing results or a '
            'proven diagnosis. Do not restart exhaustive investigation. '
            'No publication or live-addon change is permitted.\nCurrent reviewed task:\n' +
            json.dumps(context, ensure_ascii=True) + '\nInitial bounded reads: ' +
            json.dumps([dict(path=p, start=s, end=e) for p, s, e in windows], ensure_ascii=True) +
            references)


class ReadProgress:
    """A changing requested start line is not evidence of new investigation."""
    def __init__(self, *, lifetime=False):
        self.repeated = 0
        self.visible = set()
        self.known = set()
        self.lifetime = lifetime

    def edited(self,path):
        self.known={k for k in self.known if k[0]!=path}
        self.visible={k for k in self.visible if k[0]!=path}
        self.repeated=0

    def rebuild_visible(self, messages):
        self.visible = set().union(*(source_keys(m.get('content','')) or set()
            for m in messages if m.get('role') == 'tool'))

    def observe(self, name, result):
        return self.turn([(name, result)])

    @staticmethod
    def repeated_read(name, result):
        if name not in {'read_file', 'find_text', 'read_check_log'}:
            return False
        try:
            value = json.loads(result)
        except (ValueError, TypeError):
            return False  # Legacy plain source replies are still real content.
        if not isinstance(value, dict):
            return False
        return bool(value.get('already_shown_or_empty') or value.get('already_shown') or value.get('previously_shown'))

    def turn(self, results):
        # A batch is one model decision. Initial overlapping reads in a batch
        # must not count as repeated decisions before their replies are visible.
        repeated_flags = []
        for name, value in results:
            keys = source_keys(value) if name == 'read_file' else None
            if keys:
                repeated_flags.append(keys <= (self.known if self.lifetime else self.visible))
                self.visible.update(keys)
                self.known.update(keys)
            else:
                repeated_flags.append(self.repeated_read(name, value))
        repeated = results and all(repeated_flags)
        self.repeated = self.repeated + 1 if repeated else 0
        if self.repeated == 2:
            return ('These source windows were already delivered. A replayed_spans field contains '
                    'the requested current text; it is not an empty file. Do not advance one line '
                    'at a time through the same range. Use the current source and findings to make '
                    'a scoped edit, or inspect genuinely different relevant evidence. If the task '
                    'cannot proceed safely, explain the concrete missing information.')
        return None
