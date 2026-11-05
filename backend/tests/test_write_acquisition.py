"""Disposable SQLite contention: acquire once, never replay an operation."""
from concurrent.futures import ThreadPoolExecutor
import time
import pytest
from sqlalchemy.exc import OperationalError
from workbench.db import write_transaction


def test_transient_writer_contention_does_not_fail_or_replay_body(store):
    calls=[]
    def writer():
        with write_transaction(store):
            calls.append('body')
    with store.connect() as held:
        held.exec_driver_sql('BEGIN IMMEDIATE')
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending=pool.submit(writer)
            time.sleep(.3)
            assert not calls
            held.rollback()
            pending.result(timeout=3)
    assert calls==['body']


def test_persistent_writer_contention_exhausts_bounded_acquisition_without_body(store):
    calls=[]
    with store.connect() as held:
        held.exec_driver_sql('BEGIN IMMEDIATE')
        with pytest.raises(OperationalError):
            with write_transaction(store):
                calls.append('unreachable')
        held.rollback()
    assert calls==[]
