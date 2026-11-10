"""Streaming uses a bounded permission heartbeat, never one SQLite read per line."""

import time

import pytest

from workbench.real_provider import implement, BudgetError
from test_real_execution import answer


@pytest.mark.parametrize("fault", [None, "revoked", "permission_error"])
def test_many_stream_lines_do_not_multiply_database_reads_and_late_output_is_fenced(fault):
    state = dict(reads=0, fault=None)

    def permitted():
        state["reads"] += 1
        time.sleep(0.002)  # Actual retained incident had ~150ms per transaction.
        if state["fault"] == "permission_error":
            raise RuntimeError("Controlled failed permission lookup")
        return state["fault"] != "revoked"

    class Owned:
        def count_input_tokens(self, body, deadline):
            return 100

        def request(self, body, allowed, deadline):
            for _ in range(1000):
                assert allowed()
            state["fault"] = fault
            if fault:
                time.sleep(0.2)
                assert not allowed()
            return answer()

    if fault:
        with pytest.raises((BudgetError, RuntimeError)):
            implement(None, "Controlled", "", "controlled", permitted, ownership=Owned())
    else:
        assert (
            implement(None, "Controlled", "", "controlled", permitted, ownership=Owned())[
                "completion_tokens"
            ]
            == 20
        )
    assert state["reads"] < 20
