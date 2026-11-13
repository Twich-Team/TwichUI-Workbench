"""Read-only adapters for observed resource history and legacy policy overrides.

No pilot module imports, initializers, writes, service queries or HTTP collection.
The existing Workbench worker owns new 15-second observations. Legacy sources
remain read-only and retain missing metrics, collection gaps and their owner.
"""
from contextlib import contextmanager
import json
import math
from pathlib import Path
import sqlite3
import time

from workbench.catalog import clean

KEYS = ('cpu', 'gpu', 'ram_used', 'ram_total', 'vram_used', 'vram_total',
        'disk_used', 'disk_total', 'disk_available', 'swap_used', 'swap_total')
RETENTION = 7 * 86400
MAX_ROWS = RETENTION // 15 + 2


@contextmanager
def reader(path):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError('Source unavailable')
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.15)
    try:
        db.execute('PRAGMA query_only=ON')
        deadline = time.monotonic() + .5
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        db.execute('BEGIN')
        yield db
    finally:
        db.close()


def metric(value, percent=False):
    return value if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= (100 if percent else 2**60) else None


def reading(at, body):
    data = json.loads(body)
    if not isinstance(data, dict):
        raise ValueError('Invalid resource sample')
    result = {key: metric(data.get(key), key in ('cpu', 'gpu')) for key in KEYS}
    for prefix in ('ram', 'vram', 'disk', 'swap'):
        used, total = result[prefix+'_used'], result[prefix+'_total']
        if used is not None and total is not None and used > total:
            result[prefix+'_used'] = None
    if result['disk_available'] is not None and result['disk_total'] is not None and result['disk_available'] > result['disk_total']:
        result['disk_available'] = None
    return dict(at=at, **result, gpu_device=clean(data.get('gpu_device'), 120) or None)


def collector_status(db, now):
    default = dict(model=None, services=[], metric_notes={}, status_at=None, status_freshness='unavailable')
    try:
        if not db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='collector_status'").fetchone():
            return default
        row = db.execute('SELECT at,substr(body,1,4097) FROM collector_status WHERE id=1').fetchone()
        if not row or type(row[0]) is not int or len(row[1]) > 4096 or row[0] > now:
            return default
        value = json.loads(row[1])
        if value.get('collector') != 'workbench-host-v1':
            return default
        source = value.get('model') or {}
        model = dict(alias=clean(source.get('alias'),120) or None, file=clean(source.get('file'),180) or None,
                     bytes=metric(source.get('bytes')), mapped=source.get('mapped') if type(source.get('mapped')) is bool else None,
                     api=source.get('api') if source.get('api') in {'ready','authentication_required','unavailable'} else 'unavailable')
        services = []
        for item in value.get('services', [])[:5]:
            if (item.get('name') in {'api','worker','connector','private_access','model'} and
                    item.get('state') in {'active','inactive','failed','activating','deactivating','reloading','unknown'}):
                services.append(dict(name=item['name'], state=item['state'],
                    pid=item.get('pid') if type(item.get('pid')) is int and 0 <= item['pid'] < 2**31 else None,
                    restarts=item.get('restarts') if type(item.get('restarts')) is int and 0 <= item['restarts'] < 2**31 else None))
        notes = {key:clean(text,180) for key,text in value.get('metric_notes',{}).items()
                 if key in {'cpu','ram','disk','swap','gpu','vram'} and isinstance(text,str)}
        return dict(model=model, services=services, metric_notes=notes, status_at=row[0],
                    status_freshness='fresh' if now-row[0] <= 45 else 'stale')
    except (ValueError, TypeError, AttributeError, sqlite3.Error):
        return default


def resources(path, hours=1, source='observed', now=None):
    now = int(time.time() if now is None else now)
    start = now - hours * 3600
    meta = dict(source=source, label='Deterministic demo · simulated host' if source == 'demo' else 'Observed host',
                start=start, end=now, interval_s=15, retention_days=7, points=[], latest=None,
                freshness='unavailable', age_s=None, first_at=None, last_at=None,
                message='Resource source unavailable. No host readings are inferred.',
                model=None, services=[], metric_notes={}, status_at=None, status_freshness='unavailable')
    try:
        if source == 'demo':
            # A repeatable illustrative profile, never written to observed storage.
            anchor = now // 15 * 15
            raw = []
            for at in range(max(start, anchor-RETENTION), anchor+1, 15):
                phase = (at % 3600) / 3600
                if 1200 < at % 3600 < 1380:
                    continue
                raw.append((at, json.dumps(dict(cpu=22+18*math.sin(phase*6.28), gpu=43+30*math.sin(phase*6.28),
                    ram_used=(20+3*math.sin(phase*6.28))*2**30, ram_total=64*2**30,
                    vram_used=(8+2*math.sin(phase*6.28))*2**30, vram_total=16*2**30,
                    disk_used=120*2**30, disk_total=240*2**30, disk_available=108*2**30,
                    swap_used=2*2**30, swap_total=8*2**30,
                    gpu_device='Illustrative GPU'))))
            latest = raw[-1] if raw else None
            first = max(start, anchor-RETENTION)
        else:
            if not path:
                return meta
            with reader(path) as db:
                raw = db.execute('SELECT at, substr(body,1,4097) FROM samples WHERE at BETWEEN ? AND ? ORDER BY at DESC LIMIT ?',
                                 (max(start, now-RETENTION), now, MAX_ROWS+1)).fetchall()[::-1]
                latest = db.execute('SELECT at, substr(body,1,4097) FROM samples ORDER BY at DESC LIMIT 1').fetchone()
                first = db.execute('SELECT min(at) FROM samples WHERE at BETWEEN ? AND ?', (now-RETENTION, now)).fetchone()[0]
                meta.update(collector_status(db, now))
                if meta['status_at'] is not None:
                    meta['label'] = 'Observed host · Workbench collector'
        if len(raw) > MAX_ROWS or sum(len(body) for _, body in raw) > 32*1024*1024:
            raise ValueError('Read budget exceeded')
        points = []
        previous = None
        bucket = None
        step = max(15, math.ceil(hours*3600/550))
        for at, body in raw:
            if type(at) is not int or len(body) > 4096:
                raise ValueError('Invalid resource sample')
            value = reading(at, body)
            gap = previous is not None and at-previous > 45
            key = at // step
            if key != bucket:
                if gap:
                    points.append(dict(at=previous+15, **dict.fromkeys(KEYS), gpu_device=None))
                points.append(value)
                bucket = key
            else:
                # Keep unavailable measurements visible even within older buckets.
                for field in KEYS:
                    if value[field] is None or gap:
                        points[-1][field] = None
            previous = at
        current = reading(*latest) if latest and len(latest[1]) <= 4096 else None
        age = now-current['at'] if current else None
        valid_time = age is not None and 0 <= age
        return {**meta, 'points': points, 'latest': current if valid_time else None, 'age_s': age if valid_time else None,
                'first_at': first, 'last_at': raw[-1][0] if raw else None,
                'freshness': ('demo' if source == 'demo' else 'fresh' if age <= 45 else 'stale') if valid_time else 'unavailable',
                'message': 'Simulated measurements; never evidence of host or model activity.' if source == 'demo' else
                'Host-wide Linux /proc, application-filesystem and AMD DRM observations. Missing metrics and intervals remain gaps. Model identity and service health are separate read-only observations; utilization is not attributed to a model.'}
    except (OSError, ValueError, TypeError, sqlite3.Error):
        return meta


POLICY_KEYS = {'paused', 'fallback_enabled', 'invocations_per_window', 'findings_per_day', 'max_unpublished_candidates'}


def policy(path, now=None):
    now = time.time() if now is None else now
    result = dict(available=False, revision=None, observed_revision=None, observed_at=None,
                  acknowledgment='Unavailable', freshness='unavailable', values={}, observed_values={},
                  message='Pilot policy source unavailable. Editing is not enabled here.')
    if not path:
        return result
    try:
        with reader(path) as db:
            saved = db.execute('SELECT revision,substr(body,1,4097) FROM settings WHERE id=1').fetchone()
            observed = db.execute('SELECT revision,at,substr(body,1,4097) FROM observed WHERE id=1').fetchone()
        if not saved or len(saved[1]) > 4096 or (observed and len(observed[2]) > 4096):
            return result
        def values(body):
            data = json.loads(body)
            if not isinstance(data, dict):
                raise ValueError('Invalid policy')
            return {key: value for key, value in data.items() if key in POLICY_KEYS and type(value) in (bool, int) and abs(value) <= 10000}
        age = now-observed[1] if observed else None
        acknowledged = bool(observed and observed[0] == saved[0] and values(observed[2]) == values(saved[1]))
        return dict(available=True, revision=saved[0], observed_revision=observed[0] if observed else None,
                    observed_at=observed[1] if observed else None, values=values(saved[1]),
                    observed_values=values(observed[2]) if observed else {},
                    acknowledgment='Last acknowledged' if acknowledged else 'Saved · awaiting acknowledgment',
                    freshness='fresh' if age is not None and 0 <= age <= 45 else 'stale' if observed else 'unavailable',
                    message='Read-only saved overrides and last worker acknowledgment. Current worker identity and effective base policy are unconfirmed. Aged acknowledgment does not make an acknowledged revision pending. Editing is not enabled here.')
    except (OSError, ValueError, TypeError, sqlite3.Error):
        return result
