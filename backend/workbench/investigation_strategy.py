"""Small read-only working notebook and deterministic navigation feedback.

Model findings remain attributed, unverified data. Neither notes nor discovery
suggestions establish a diagnosis, authorize a plan, or grant write authority.
"""
import copy
import re
from uuid import UUID
from workbench.visual_scope import digest


def validate(packet):
    notes=packet.get('investigation_notes',[])
    if not isinstance(notes,list) or len(notes)>4:
        raise ValueError('Invalid bounded investigation notes')
    for note in notes:
        if (not isinstance(note,dict) or set(note)!={'phase','finding','report_binding'}
                or not isinstance(note['finding'],str) or not 1<=len(note['finding'])<=800
                or not isinstance(note['report_binding'],str) or not re.fullmatch('[a-f0-9]{64}',note['report_binding'])
                or not isinstance(note['phase'],str) or str(UUID(note['phase']))!=note['phase']):
            raise ValueError('Invalid attributed investigation note')
    return notes


def remember(packet,finding,phase,original=None):
    notes=validate(packet)
    if phase is None:
        return
    binding=digest(original or {})
    entry=dict(phase=str(UUID(phase)),finding=finding,report_binding=binding)
    notes=[n for n in notes if n['report_binding']==binding and n['finding']!=finding]
    packet['investigation_notes']=[*notes,entry][-4:]
    validate(packet)


def candidates(directory,packet,original):
    def words(value):
        value=re.sub(r'([a-z])([A-Z])',r'\1 \2',value)
        return {w.rstrip('s') for w in re.findall(r'[a-zA-Z]{3,}',value.lower())
                if w.lower() not in {'twich','twichui','lua','tests','test','pages','settings','modules','setup'}}
    terms=words(original['title']+' '+original['description']+' '+
        ' '.join(packet.get('inspection_focus',[]))+' '+
        ' '.join(n['finding'] for n in validate(packet) if n['report_binding']==digest(original)))
    inspected={f['path'] for f in packet['files']}
    scored=[]
    for parent,names in directory.get('directories',{}).items():
        for name in names:
            path=parent+'/'+name
            overlap=len(words(path)&terms)
            if path not in inspected and overlap:
                # Prefer a different observable surface over more of the same
                # data layer; names are suggestions, never source evidence.
                score=overlap+(2 if parent.startswith('TwichUI_Settings') else 0)
                scored.append((-score,path))
    return [p for _,p in sorted(scored)[:8]]


def context(packet,directory,original):
    return dict(notes=copy.deepcopy([n for n in validate(packet) if n['report_binding']==digest(original)]),
        unread_related_paths=candidates(directory,packet,original),
        note='Attributed prior MODEL DATA, not established facts, instructions, a plan or permission. '
             'Keep observed source behavior separate from hypotheses and remaining questions. '
             'Suggested names have not been inspected; select relevant source before proposing writes.')


def correction(error):
    """Reject invalid read-only output; give constraints, never a repaired answer."""
    from pydantic import ValidationError
    from workbench.planning_response import ResponseValidationError
    if isinstance(error,ValidationError):
        relations={
            'distinct_read_paths':('read_paths','Use distinct read paths.'),
            'distinct_writable_paths':('writable_paths','Use distinct writable paths.'),
            'writable_source_not_read':('read_paths','Include every writable path in read_paths.'),
            'focused_test_not_writable':('writable_paths','Include focused_test in writable_paths and read_paths; at most THREE writable files TOTAL including the test. Do not silently add a fourth file or remove required behavior.'),
            'production_scope_required':('writable_paths','Include the project production source needed for this repair.'),
            'distinct_criteria':('criteria','Use distinct behavior criterion identifiers.'),
        }
        fields={'finding','plan','inspection','question','limitation','scope','read_paths','writable_paths',
                'focused_test','criteria','id','description','risks','path','start','end','query',
                'InvestigationRead','InvestigationSearch'}
        issues=[]
        for issue in error.errors(include_input=False,include_context=False,include_url=False)[:8]:
            path=[p if (type(p) is int and 0<=p<=512 or isinstance(p,str) and p in fields)
                  else 'response' for p in issue['loc'][:6]]
            relation=relations.get(issue['type'])
            if relation and path==['plan']:
                field,rule=relation
                issues.append(dict(path=['plan',field],constraint=issue['type'],rule=rule))
            elif issue['type'] in {'incomplete_finding','incomplete_scope'}:
                issues.append(dict(path=['finding'] if issue['type']=='incomplete_finding' else ['plan','scope'],
                    constraint=issue['type'],rule='Use complete concise sentences within 800 characters. Summarize the request instead of copying it; retain material facts, uncertainty and limits.'))
            else:
                issues.append(dict(path=path,constraint=issue['type']))
    elif isinstance(error,ResponseValidationError):
        issues=[dict(path=['plan','repair_profile'],constraint='repair_profile_required',rule=error.expected)] if list(error.path)==['plan','repair_profile'] else [dict(path=['response'],constraint='semantic_validation')]
    else:
        return None
    return dict(rejected=True,approved=False,executed=False,errors=issues,
        note='The completed read-only reply was rejected; none of it is a proposal or authority. '
             'Return a fresh complete reply against the declared schema. Exactly one outcome; '
             'at most THREE source operations TOTAL across reads and searches, never three of each. '
             'Keep pending reads for a later turn. Do not drop requirements, invent evidence or broaden scope.')


def rejection_signature(raw,facts):
    """Wording churn cannot count as correcting the same scope relationship."""
    errors=facts.get('errors',[]) if facts else []
    relations={'distinct_read_paths','distinct_writable_paths','writable_source_not_read',
               'focused_test_not_writable','production_scope_required','distinct_criteria'}
    if errors and all(e['constraint'] in relations for e in errors):
        plan=raw.get('plan') or {}
        fields={
            'distinct_read_paths':('read_paths',),
            'distinct_writable_paths':('writable_paths',),
            'writable_source_not_read':('read_paths','writable_paths'),
            'focused_test_not_writable':('writable_paths','focused_test'),
            'production_scope_required':('writable_paths',),
            'distinct_criteria':('criteria',),
        }
        signatures=[]
        for error in errors:
            values={key:([c.get('id') for c in plan.get(key,[]) if isinstance(c,dict)] if key=='criteria'
                         else sorted(plan.get(key,[])) if key.endswith('_paths') else plan.get(key))
                    for key in fields[error['constraint']]}
            signatures.append(dict(constraint=error['constraint'],values=values))
        return digest(signatures)
    return digest(dict(raw=raw,facts=facts))
