import os
import secrets
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import JSONResponse, FileResponse, Response
from fastapi.exceptions import RequestValidationError
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from workbench import verification, admission, implementation, pr_preparation, private_lan
from workbench import catalog, monitoring, preferences, intake, model_policy, model_accounting
from workbench.schemas import DisplaySettings, Resources, Policy, ActivityDistribution
from workbench.schemas import (ArtifactReferenceInput, FeatureAttachmentUpload, FeatureAttachmentView,
    FeatureReferenceLookupInput, FeatureReferenceLookupView)
from workbench.schemas import CatalogPage, CatalogDetail, WorkOverview, HistoryPage
from workbench.db import commands, engine_for, tasks
from workbench.schemas import (Decision, Problem, Receipt, Session, Task, IntakeInput, IntakeView,
    IntakePage, ProposalDecisionInput, ProposalReceipt, ArtifactInput, PlanningRetry,
    ClarificationInput, ResearchRevision, PriorityInput, ScopeRevisionInput, InvestigationStart, ScopeRecoveryInput)
from workbench.service import get_task, submit, task_view
from workbench.real_execution import RunPage, StopRequest
from workbench.task_execution import TaskExecutionContext
from workbench.workspace_view import WorkspacePage
from workbench import work_controls
from workbench import feature_workshop, feature_recovery, feature_permission
from workbench import feature_scope_recovery
from workbench import feature_run_recovery

# Exact authorities only. The dedicated HTTPS service admits the configured home subnet.
AUTHORITIES = {"127.0.0.1:8787", "localhost:8787", "127.0.0.1:5180", "localhost:5180",
               "127.0.0.1:9187", "127.0.0.1:5280", "127.0.0.1:9287", "127.0.0.1:5380", "127.0.0.1:9387", "127.0.0.1:9487", "testserver"}


def create_app(engine=None, pilot_path=None, resources_path=None, policy_path=None, capabilities_path=None):
    app = FastAPI(title="TwichUI Workbench fixture API", version="0.1.0")
    app.state.engine = engine or engine_for()
    app.state.pilot_path = pilot_path if pilot_path is not None else os.environ.get("WORKBENCH_PILOT_DB")
    app.state.resources_path = resources_path if resources_path is not None else os.environ.get('WORKBENCH_RESOURCE_DB')
    if app.state.resources_path is None and os.environ.get('WORKBENCH_PLANNER') == 'everyday':
        from workbench.host_resources import database as resource_database
        app.state.resources_path = resource_database(app.state.engine.url.database)
    app.state.policy_path = policy_path if policy_path is not None else os.environ.get('WORKBENCH_POLICY_DB')
    app.state.capabilities_path = capabilities_path
    token = secrets.token_urlsafe(32)
    lan = private_lan.configured()

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        hosts = request.headers.getlist("host")
        origins = request.headers.getlist("origin")
        portal = bool(lan and request.client and private_lan.is_portal(lan, request.client.host))
        authority = private_lan.PORTAL_AUTHORITY if portal else (lan[0] if lan else None)
        if len(hosts) != 1 or (hosts[0] != authority if lan else hosts[0] not in AUTHORITIES):
            return JSONResponse({"detail": "Unrecognized host."}, status_code=400)
        if lan and (not request.client or not (portal or private_lan.allows_peer(lan[1], request.client.host))
                    or request.scope['scheme'] != 'https'):
            return JSONResponse({"detail": "Private Workbench access is restricted."}, status_code=403)
        expected_origin = ('https://' if lan else 'http://') + hosts[0]
        if origins and (len(origins) != 1 or origins[0] != expected_origin):
            return JSONResponse({"detail": "Use this application's own address."}, status_code=403)
        if request.method not in {"GET", "HEAD"}:
            supplied = request.headers.get("x-csrf-token", "")
            if (origins != [expected_origin] or
                request.headers.get("sec-fetch-site") not in {None, "same-origin"} or
                not secrets.compare_digest(supplied, token)):
                return JSONResponse({"detail": "Your session changed. Reload and try again."}, status_code=403)
            if request.headers.get("content-type", "").split(";")[0] != "application/json":
                return JSONResponse({"detail": "JSON is required."}, status_code=415)
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                limit = 5600000 if request.url.path == '/api/feature-attachments' else 1420000 if request.url.path.endswith('/artifacts') else 16384
                if len(body) > limit:
                    return JSONResponse({"detail": "Request is too large."}, status_code=413)
            request._body = bytes(body)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'; base-uri 'self'; object-src 'none'")
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request, _error):
        return JSONResponse(status_code=422, content={
            "detail": "Choose a supported range (1, 24, 168 hours) and refresh (15, 30, 60 seconds)." if _request.url.path == '/api/display-settings' else "Check required fields and input limits." if _request.url.path.startswith('/api/intake') else "Check model preferences, unique fallback order and supported model/effort choices. Activation is unavailable." if _request.url.path.startswith('/api/models') else "Check the review fields. Answer every behavior and keep notes within 1600 characters."})

    @app.exception_handler(OperationalError)
    async def database_error(_request, error):
        # SQLITE_BUSY / SQLITE_LOCKED are temporary; other failures need operator attention.
        busy = getattr(error.orig, "sqlite_errorcode", 0) & 0xFF in {5, 6}
        return JSONResponse(status_code=503, headers={"Retry-After": "1"}, content={
            "detail": "The review store is busy. Retry the same decision in a moment." if busy else
                      "The review store is unavailable. Your saved decisions remain recorded."})

    errors = {409: {"model": Problem}, 422: {"model": Problem}, 503: {"model": Problem}}

    @app.get("/api/session", response_model=Session)
    def session():
        return {"csrf_token": token}

    from workbench.everyday import LocalWorkStatus

    @app.get('/api/local-work', response_model=LocalWorkStatus)
    def local_work():
        from workbench.everyday import status
        return status(app.state.engine)

    @app.get('/api/intake', response_model=IntakePage)
    def intake_list(page: int = Query(default=1, ge=1, le=1000)):
        return intake.listing(app.state.engine, page)

    @app.get('/api/features', response_model=feature_workshop.FeaturePage)
    def features(page: int = Query(default=1, ge=1, le=1000), archived: bool = False):
        return feature_workshop.listing(app.state.engine, page, archived)

    @app.post('/api/feature-attachments', response_model=FeatureAttachmentView, responses=errors)
    def upload_feature_reference(body: FeatureAttachmentUpload):
        from workbench import feature_attachments
        try:
            return feature_attachments.upload(app.state.engine, body)
        except (ValueError, OSError):
            raise HTTPException(409, 'Private reference storage is unavailable or changed. Your original file was not overwritten.') from None

    @app.get('/api/feature-attachments/{request_id}/{attachment_id}/metadata', response_model=FeatureAttachmentView)
    def feature_attachment_metadata(request_id: str, attachment_id: str):
        from workbench import feature_attachments
        try:
            value, _ = feature_attachments.load(app.state.engine, request_id, attachment_id)
            return feature_attachments.view(value)
        except (ValueError, OSError, KeyError, TypeError):
            raise HTTPException(404, 'This original feature reference is unavailable.') from None

    @app.get('/api/feature-attachments/{request_id}/{attachment_id}')
    def feature_attachment_content(request_id: str, attachment_id: str):
        from workbench import feature_attachments
        try:
            value, data = feature_attachments.load(app.state.engine, request_id, attachment_id)
            media_type = value['media_type']
            if media_type not in {'image/png', 'text/plain'}:
                raise ValueError('Unsupported passive reference')
        except (ValueError, OSError, KeyError, TypeError):
            raise HTTPException(404, 'This original feature reference is unavailable.') from None
        return Response(data, media_type=media_type, headers={
            'Content-Disposition': 'inline; filename="feature-reference.png"' if media_type=='image/png' else 'attachment; filename="feature-reference.txt"',
            'X-Content-Type-Options': 'nosniff',
            'Content-Security-Policy': "sandbox; default-src 'none'; frame-ancestors 'none'"})

    @app.post('/api/feature-references/lookup', response_model=FeatureReferenceLookupView, responses=errors)
    def lookup_feature_reference(body: FeatureReferenceLookupInput):
        from workbench import feature_references
        try:
            return feature_references.lookup_view(app.state.engine, body)
        except (ValueError, OSError, KeyError, TypeError):
            raise HTTPException(409, 'Reference lookup is busy or unavailable. Keep your links and try again.') from None

    @app.post('/api/feature-references/evidence', response_model=FeatureReferenceLookupView, responses=errors)
    def saved_feature_reference(body: FeatureReferenceLookupInput):
        from workbench import feature_references
        from workbench.visual_scope import digest
        try:
            values = feature_references.evidence(app.state.engine,body.request_id,[r.model_dump(mode='json') for r in body.references])
            if len(values)!=len(body.references):
                raise ValueError('Reference evidence unavailable')
            return dict(binding=digest(values),references=values,message='Saved reference evidence. No new lookup ran.')
        except (ValueError, OSError, KeyError, TypeError):
            raise HTTPException(404, 'Saved reference evidence is unavailable. No lookup or model call was started.') from None

    @app.get('/api/features/{identity}', response_model=feature_workshop.FeatureView)
    def feature_detail(identity: str):
        return feature_workshop.view(app.state.engine, identity)

    @app.post('/api/features/{identity}/correct-steps', response_model=feature_workshop.FeatureView, responses=errors)
    def correct_feature_steps(identity: str, body: feature_recovery.CorrectionInput):
        feature_recovery.request(app.state.engine, identity, body)
        return feature_workshop.view(app.state.engine, identity)

    @app.post('/api/features/{identity}/prepare-step-again', response_model=feature_workshop.FeatureView, responses=errors)
    def continue_feature_preparation(identity: str, body: feature_scope_recovery.FeatureScopeCorrectionInput):
        feature_scope_recovery.request(app.state.engine, identity, body)
        return feature_workshop.view(app.state.engine, identity)

    @app.get('/api/features/{identity}/run-offer', response_model=feature_workshop.FeatureRunAvailability)
    def feature_run_offer(identity: str):
        return feature_workshop.run_offer(app.state.engine, identity)

    @app.post('/api/features/{identity}/continue-unstarted-run', response_model=feature_workshop.FeatureView, responses=errors)
    def continue_unstarted_feature(identity: str, body: feature_run_recovery.RunRecoveryInput):
        feature_run_recovery.request(app.state.engine,identity,body)
        return feature_workshop.view(app.state.engine,identity)

    @app.post('/api/features/{identity}/run', response_model=ProposalReceipt, responses=errors)
    def run_feature(identity: str, body: feature_permission.FeatureRunInput):
        return feature_permission.request(app.state.engine, identity, body)

    @app.post('/api/features/{identity}/runs/{decision_id}/stop', responses=errors)
    def stop_feature(identity: str, decision_id: str, body: StopRequest):
        return feature_permission.stop(app.state.engine, identity, decision_id, str(body.command_id))

    @app.post('/api/intake', response_model=IntakeView, responses=errors)
    def intake_submit(body: IntakeInput):
        return intake.submit(app.state.engine, body)

    @app.get('/api/intake/{identity}', response_model=IntakeView)
    def intake_detail(identity: str):
        return intake.view(app.state.engine, identity)

    @app.post('/api/intake/{identity}/decisions', response_model=ProposalReceipt, responses=errors)
    def proposal_decide(identity: str, body: ProposalDecisionInput):
        return intake.decide(app.state.engine, identity, body)

    @app.get('/api/intake/{identity}/tasks/{task_id}/admission', response_model=admission.AdmissionContext)
    def admission_context(identity: str, task_id: str):
        return admission.context(app.state.engine, identity, task_id)

    @app.get('/api/intake/{identity}/tasks/{task_id}/execution', response_model=TaskExecutionContext)
    def task_execution_context(identity: str, task_id: str):
        from workbench.task_execution import context
        return context(app.state.engine, identity, task_id)

    @app.post('/api/intake/{identity}/tasks/{task_id}/admission', response_model=admission.AdmissionReceipt, responses=errors)
    def request_admission(identity: str, task_id: str, body: admission.FixtureAdmissionRequest):
        return admission.submit(app.state.engine, identity, task_id, body)

    @app.get('/api/intake/{identity}/tasks/{task_id}/candidates/{candidate_id}/review', response_model=pr_preparation.PreparationContext)
    def candidate_review(identity: str, task_id: str, candidate_id: str):
        return pr_preparation.context(app.state.engine, identity, task_id, candidate_id)

    @app.post('/api/intake/{identity}/tasks/{task_id}/candidates/{candidate_id}/sample-pr', response_model=pr_preparation.PreparationReceipt, responses=errors)
    def prepare_sample_pr(identity: str, task_id: str, candidate_id: str, body: pr_preparation.PreparationRequest):
        return pr_preparation.submit(app.state.engine, identity, task_id, candidate_id, body)

    @app.get('/api/intake/{identity}/tasks/{task_id}/implementation', response_model=implementation.ImplementationContext)
    def implementation_context(identity: str, task_id: str):
        return implementation.context(app.state.engine, identity, task_id)

    @app.post('/api/intake/{identity}/tasks/{task_id}/implementation', response_model=implementation.ImplementationReceipt, responses=errors)
    def request_sample(identity: str, task_id: str, body: implementation.SimulatedImplementationRequest):
        return implementation.submit(app.state.engine, identity, task_id, body)

    @app.post('/api/intake/{identity}/tasks/{task_id}/candidates/{candidate_id}/verification', response_model=implementation.CandidateVerificationReceipt, responses=errors)
    def verify_sample(identity: str, task_id: str, candidate_id: str, body: implementation.CandidateVerificationRequest):
        return implementation.submit_verification(app.state.engine, identity, task_id, candidate_id, body)

    @app.post('/api/intake/{identity}/artifacts', response_model=IntakeView, responses=errors)
    def proposal_attach(identity: str, body: ArtifactInput):
        intake.attach(app.state.engine, identity, body)
        return intake.view(app.state.engine, identity)

    @app.post('/api/intake/{identity}/reference-artifacts', response_model=IntakeView, responses=errors)
    def attach_original_reference(identity: str, body: ArtifactReferenceInput):
        intake.attach_reference(app.state.engine, identity, body)
        return intake.view(app.state.engine, identity)

    @app.get('/api/planning-artifacts/{identity}')
    def planning_artifact(identity: str):
        with app.state.engine.connect() as connection:
            artifact = intake.require(connection, intake.proposal_artifacts, identity)
        return Response(artifact['data'], media_type=artifact['media_type'], headers={
            'Content-Disposition': 'inline; filename="planning-reference.png"' if artifact['media_type']=='image/png' else 'attachment; filename="evidence.txt"',
            'Content-Security-Policy': "sandbox; default-src 'none'; frame-ancestors 'none'"})

    @app.post('/api/intake/{identity}/retry', response_model=IntakeView, responses=errors)
    def planning_retry(identity: str, body: PlanningRetry):
        intake.retry(app.state.engine, identity, body.job_id, preparation=body)
        return intake.view(app.state.engine, identity)

    @app.post('/api/intake/{identity}/investigation', response_model=IntakeView, responses=errors)
    def investigation_start(identity: str, body: InvestigationStart):
        from workbench.investigation_start import start
        start(app.state.engine, identity, body)
        return intake.view(app.state.engine, identity)

    @app.post('/api/intake/{identity}/clarification', response_model=IntakeView, responses=errors)
    def planning_clarify(identity: str, body: ClarificationInput):
        intake.clarify(app.state.engine, identity, body)
        return intake.view(app.state.engine, identity)

    @app.post('/api/intake/{identity}/scope', response_model=IntakeView, responses=errors)
    def planning_scope(identity: str, body: ScopeRevisionInput):
        intake.revise_scope(app.state.engine, identity, body)
        return intake.view(app.state.engine, identity)

    @app.post('/api/intake/{identity}/scope-recovery', response_model=IntakeView, responses=errors)
    def planning_scope_recovery(identity: str, body: ScopeRecoveryInput):
        from workbench.scope_binding import recover
        recover(app.state.engine, identity, body)
        return intake.view(app.state.engine, identity)

    @app.post('/api/intake/{identity}/research-revision', response_model=IntakeView, responses=errors)
    def planning_research(identity: str, body: ResearchRevision):
        if not body.explanation.strip():
            raise HTTPException(422, 'Explain the research scope.')
        intake.research_revision(app.state.engine, identity, body)
        return intake.view(app.state.engine, identity)

    @app.put('/api/intake/{identity}/priority', response_model=IntakeView, responses=errors)
    def planning_priority(identity: str, body: PriorityInput):
        intake.set_priority(app.state.engine, identity, body.priority)
        return intake.view(app.state.engine, identity)

    @app.get('/api/models', response_model=model_policy.ModelOverview)
    def models():
        return model_policy.overview(app.state.engine, model_policy.capabilities(app.state.capabilities_path))

    @app.put('/api/models/policy', response_model=model_policy.ExecutionPolicy, responses=errors)
    def save_model_policy(body: model_policy.ExecutionPolicy):
        return model_policy.save(app.state.engine, body, model_policy.capabilities(app.state.capabilities_path))

    @app.get('/api/models/preview', response_model=model_policy.RoutingDecision)
    def preview_model_routing(case: Literal['saved','fallback','capacity','disabled','stale','unknown','verification','limit'] = 'saved',
                              purpose: Literal['planning','decomposition','summary','implementation','other'] = 'planning'):
        return model_policy.preview(model_policy.read(app.state.engine), model_policy.capabilities(app.state.capabilities_path), case, purpose)

    from workbench import usage_view

    @app.get('/api/usage', response_model=usage_view.UsageOverview)
    def usage(days: int = Query(default=7,ge=7,le=90)):
        if days not in {7,30,90}:
            raise HTTPException(422, 'Choose 7, 30 or 90 days.')
        return usage_view.read(app.state.engine, days)

    @app.get('/api/models/activity', response_model=model_accounting.Accounting)
    def model_activity(source: Literal['observed','demo'] = 'observed'):
        if source == 'demo':
            from workbench.model_activity_demo import activity
            return activity()
        return model_accounting.read(app.state.engine)

    @app.get('/api/display-settings', response_model=DisplaySettings)
    def display_settings():
        return preferences.read(app.state.engine)

    @app.put('/api/display-settings', response_model=DisplaySettings, responses=errors)
    def save_display_settings(body: DisplaySettings):
        return preferences.save(app.state.engine, body)

    @app.get('/api/resources', response_model=Resources)
    def resources(source: Literal['demo', 'observed'] = 'observed', hours: int = 1):
        if hours not in {1, 24, 168}:
            raise HTTPException(422, 'Choose 1, 24 or 168 hours.')
        return monitoring.resources(app.state.resources_path, hours, source)

    @app.get('/api/work-controls', response_model=work_controls.WorkControl)
    def work_status():
        return work_controls.read(app.state.engine)

    @app.put('/api/work-controls', response_model=work_controls.WorkControl, responses=errors)
    def save_work_status(body: work_controls.WorkControlInput):
        return work_controls.save(app.state.engine, body)

    @app.get('/api/operating-policy', response_model=Policy)
    def operating_policy():
        return monitoring.policy(app.state.policy_path)

    @app.get('/api/activity', response_model=ActivityDistribution)
    def activity(source: str = 'fixture', demo: Literal['simple','mixed','hierarchy','reviews','empty'] = 'simple'):
        meta, rows = catalog.snapshot(app.state.engine, source, app.state.pilot_path, demo)
        counts = {}
        for row in rows:
            if row['kind'] in {'task', 'finding'} and (source == 'fixture' or row['currently_observed']) and (row['state'].casefold().startswith(('waiting', 'blocked'))):
                counts[row['state']] = counts.get(row['state'], 0) + 1
        return {**meta, 'counts': counts, 'basis': 'Current explicit waiting/blocked task and finding states in the bounded snapshot. Containers and stale records excluded; not a time series.',
                'omissions': ['Task completions over time omitted: projection updates and passed checks are not recorded completion events.',
                              'Attempt outcomes over time omitted: retained attempt summaries lack authoritative event times and unique attempt identities.']}

    @app.get("/api/tasks", response_model=list[Task])
    def list_tasks():
        with app.state.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN")
            return [task_view(connection, row) for row in
                    connection.execute(select(tasks).order_by(tasks.c.id)).mappings()]

    @app.get('/api/tasks/{task_id}/verification', response_model=list[verification.VerificationReceipt])
    def verification_history(task_id: str):
        return verification.listing(app.state.engine, task_id)

    @app.post('/api/tasks/{task_id}/verification', response_model=verification.VerificationReceipt, responses=errors)
    def verify_fixture(task_id: str, body: verification.FixtureVerificationRequest):
        return verification.submit(app.state.engine, task_id, body)

    @app.get("/api/tasks/{task_id}", response_model=Task)
    def detail(task_id: str):
        with app.state.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN")
            return task_view(connection, get_task(connection, task_id))

    @app.post("/api/tasks/{task_id}/decisions", response_model=Receipt, responses=errors)
    def decide(task_id: str, body: Decision):
        return submit(app.state.engine, task_id, body)

    @app.get("/api/commands/{command_id}", response_model=Receipt)
    def receipt(command_id: str):
        with app.state.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN")
            row = connection.execute(select(commands).where(commands.c.id == command_id)).mappings().first()
            if row is None:
                raise HTTPException(404, "No saved receipt was found for that command.")
            return row

    @app.get('/api/work', response_model=WorkOverview)
    def work(source: str = 'fixture', demo: Literal['simple','mixed','hierarchy','reviews','empty'] = 'simple'):
        meta, rows = catalog.snapshot(app.state.engine, source, app.state.pilot_path, demo)
        intake_needs, intake_pending = intake.pending_counts(app.state.engine) if source == 'fixture' else (0, 0)
        current = [r for r in rows if r['currently_observed'] and r['working'] and r['kind'] in {'task', 'finding'}]
        allowed = meta['freshness']['state'] in {'fixture', 'fresh'}
        return {**meta, 'current': current[:6],
                'waiting': [r for r in rows if r['currently_observed'] and r['state'].casefold().startswith('waiting')][:6],
                'needs_you': sum(r['state'] in {'Needs your review','Needs you','Needs another look'} and
                                 (source == 'fixture' or r['currently_observed']) for r in rows) + intake_needs if allowed else 0,
                'waiting_application': sum(r['state']=='Decision saved · Waiting to apply' for r in rows) + intake_pending,
                'current_message': 'Recorded working items; active inference is not confirmed.' if current else
                'No implementation is running in this example workspace.' if source == 'fixture' else
                'No working item in the fresh projection; model activity is not confirmed.' if allowed else
                'Current work is unavailable. Last-known records remain browsable.'}

    @app.get('/api/backlog', response_model=CatalogPage)
    def backlog(source: str = 'fixture', parent: str | None = Query(default=None, max_length=160),
                q: str = Query(default='', max_length=200), page: int = Query(default=1, ge=1, le=1000),
                view: str = Query(default='open', pattern='^(open|all|needs_you|working|waiting|finished)$'),
                demo: Literal['simple','mixed','hierarchy','reviews','empty'] = 'simple',
                type: list[str] = Query(default=[]), state: list[str] = Query(default=[]),
                area: list[str] = Query(default=[]), sort: Literal['title','updated'] = 'title',
                focus: str | None = Query(default=None, max_length=160)):
        return catalog.browse(app.state.engine, source, app.state.pilot_path, parent, q, page, view, demo, type, state, area, sort, focus)

    @app.get('/api/records/{source}/{record_id}', response_model=CatalogDetail)
    def record_detail(source: str, record_id: str, demo: Literal['simple','mixed','hierarchy','reviews','empty'] = 'simple'):
        meta, rows = catalog.snapshot(app.state.engine, source, app.state.pilot_path, demo)
        by_id = {r['id']:r for r in rows}
        row = by_id.get(record_id)
        if row is None:
            raise HTTPException(404, 'This record is unavailable in the selected source. Refresh the list.')
        if source == 'pilot' and not row['currently_observed']:
            meta = {**meta, 'freshness':dict(state='stale', observed_at=row['observed_at'],
                    message='This record has no fresh observation. Its state is last-known, not current execution or eligibility.')}
        children = [r for r in rows if r['parent'] == record_id]
        ancestors = []
        parent = row['parent']
        while parent:
            ancestors.insert(0, by_id[parent])
            parent = by_id[parent]['parent']
        return {**meta, 'record':row, 'ancestors':ancestors, 'children':children[:12], 'children_total':len(children)}

    @app.get('/api/history', response_model=HistoryPage)
    def history(source: str = 'fixture', page: int = Query(default=1, ge=1, le=1000),
                demo: Literal['simple','mixed','hierarchy','reviews','empty'] = 'simple'):
        return catalog.history(app.state.engine, source, app.state.pilot_path, page, demo)

    @app.get('/api/workspace', response_model=WorkspacePage)
    def everyday_workspace(page: int = Query(default=1, ge=1, le=10000),
                           search: str = Query(default='', max_length=160),
                           state: str = Query(default='all', max_length=40),
                           kind: str = Query(default='all', max_length=40),
                           sort: str = Query(default='newest', max_length=16),
                           result_view: str = Query(default='all', max_length=16),
                           archive_scope: Literal['current', 'archived', 'all'] = 'current',
                           page_size: int = Query(default=12, ge=1, le=100)):
        from workbench.workspace_view import listing
        return listing(app.state.engine, page=page, search=search, state=state, kind=kind, page_size=page_size, sort=sort, result_view=result_view, archive_scope=archive_scope)

    from workbench import task_cleanup

    @app.get('/api/intake/{identity}/cleanup', response_model=task_cleanup.CleanupContext)
    def cleanup_review(identity: str):
        return task_cleanup.context(app.state.engine, identity)

    @app.post('/api/task-cleanup', response_model=task_cleanup.CleanupResult, responses=errors)
    def cleanup_tasks(body: task_cleanup.CleanupCommand):
        return task_cleanup.apply(app.state.engine, body)

    @app.get('/api/task-cleanup/{identity}', response_model=task_cleanup.CleanupResult)
    def cleanup_receipt(identity: str):
        return task_cleanup.receipt(app.state.engine, identity)

    @app.get('/api/supported-tasks')
    def supported_task_catalog():
        from workbench.supported_tasks import catalog
        return catalog(app.state.engine)

    from workbench import draft_delivery

    @app.get('/api/real-executions/{identity}/draft-review')
    def checked_draft_review(identity: str):
        return draft_delivery.review(app.state.engine, identity)

    @app.post('/api/real-executions/{identity}/draft-delivery', responses=errors)
    def request_checked_draft(identity: str, body: draft_delivery.DraftRequest):
        try:
            return draft_delivery.request(app.state.engine, identity, body.binding)
        except ValueError as error:
            raise HTTPException(409, str(error)) from None

    @app.get('/api/real-executions/{identity}/delivery')
    def delivery_history(identity: str):
        from workbench.real_publication import view
        return view(app.state.engine, identity)

    @app.get('/api/intake/{identity}/proposals/{proposal_id}/run-offer')
    def run_offer(identity: str, proposal_id: str):
        from workbench.task_budget import context
        return context(app.state.engine, identity, proposal_id)

    @app.get('/api/intake/{identity}/tasks/{task_id}/run-approval')
    def task_run_approval(identity: str, task_id: str):
        from workbench.task_budget import run_context
        return run_context(app.state.engine, identity, task_id)

    from workbench.schemas import TaskRunApprovalInput

    @app.post('/api/intake/{identity}/tasks/{task_id}/run-approval', response_model=ProposalReceipt, responses=errors)
    def approve_task_run(identity: str, task_id: str, body: TaskRunApprovalInput):
        from workbench.task_budget import approve_run
        from workbench.task_execution import Waiting
        try:
            return approve_run(app.state.engine, identity, task_id, body)
        except (Waiting, ValueError) as error:
            raise HTTPException(409, str(error)) from None

    @app.get('/api/real-executions', response_model=RunPage)
    def real_execution_listing(page: int = Query(default=1, ge=1, le=1000),
                               group: Literal['all','active','attention','checked'] = 'all'):
        from workbench.real_execution import listing
        return listing(app.state.engine, page, group)

    @app.get('/api/real-executions/{identity}')
    def real_execution_review(identity: str):
        from workbench.real_execution import review
        return review(app.state.engine, identity)

    from workbench.simulator_comparison import ComparisonView

    @app.get('/api/real-executions/{identity}/simulator', response_model=ComparisonView)
    def simulator_comparison(identity: str):
        from workbench.simulator_comparison import view
        return view(app.state.engine, identity)

    @app.get('/api/real-executions/{identity}/simulator/{which}')
    def simulator_capture(identity: str, which: Literal['before', 'after']):
        from workbench.simulator_comparison import image
        return Response(image(app.state.engine, identity, which), media_type='image/png',
            headers={'X-Content-Type-Options':'nosniff', 'Cache-Control':'no-store',
                     'Content-Security-Policy':"default-src 'none'; sandbox"})

    @app.post('/api/real-executions/{identity}/stop', responses=errors)
    def stop_real_execution(identity: str, body: StopRequest):
        from workbench.real_execution import cancel
        return cancel(app.state.engine, identity, str(body.command_id))

    @app.post('/api/task-sessions/{identity}/stop', responses=errors)
    def stop_task_session(identity: str, body: StopRequest):
        from workbench.task_sessions import cancel
        return cancel(app.state.engine, identity, str(body.command_id))

    # Optional built frontend: no pilot, database, arbitrary filesystem or fixture routes.
    static_root = Path(os.environ.get("WORKBENCH_STATIC_ROOT", Path(__file__).resolve().parents[2] / "frontend/dist")).resolve()
    if static_root.exists() and not os.environ.get("WORKBENCH_API_ONLY"):
        @app.get("/{path:path}", include_in_schema=False)
        def frontend(path: str):
            if path.startswith(("api/", ".")):
                raise HTTPException(404)
            file = (static_root / path).resolve()
            if not file.is_relative_to(static_root.resolve()):
                raise HTTPException(404)
            return FileResponse(file if file.is_file() else static_root / "index.html")

    return app


app = create_app()
