"""Real subprocess faults separate content validity from provider lifecycle."""

import json
import sys
import time

import pytest

from test_codex_provider import Transcript, completed
from workbench import codex_provider as codex
from workbench.codex_journal import Journal


@pytest.mark.parametrize(
    "fault", ["lost-start-reply", "whitespace", "malformed-complete", "disconnect"]
)
def test_independent_lifecycle_survives_content_start_reply_and_disconnect_faults(tmp_path, fault):
    cli = tmp_path / "controlled_cli.py"
    calls = tmp_path / "calls.jsonl"
    cli.write_text("""import json,sys
responses=json.loads(sys.argv[1]);fault=sys.argv[4]
def send(x):print(json.dumps(x),flush=True)
for line in sys.stdin:
 r=json.loads(line);m=r.get('method')
 with open(sys.argv[2],'a') as out:out.write(json.dumps(m)+'\\n')
 if m=='initialized':continue
 if m=='turn/interrupt':
  print('MALFORMED FRAME',flush=True)
  send({'id':r['id'],'result':{}})
  send(json.loads(sys.argv[3]))
  send({'method':'turn/completed','params':{'threadId':'thread','turnId':'turn','turn':{'id':'turn','status':'interrupted'}}})
 elif m=='turn/start':
  send({'method':'turn/started','params':{'threadId':'thread','turn':{'id':'turn','status':'inProgress'}}})
  if fault=='disconnect':break
  if fault=='lost-start-reply':continue
  send({'id':r['id'],'result':responses[m]})
  if fault=='whitespace':send({'method':'item/agentMessage/delta','params':{'threadId':'thread','turnId':'turn','delta':' '*1100}})
  else:
   send({'method':'item/completed','params':{'item':{'type':'agentMessage','text':'{broken JSON','phase':'final_answer'}}})
   send(json.loads(sys.argv[3]))
   send({'method':'turn/completed','params':{'threadId':'thread','turnId':'turn','turn':{'id':'turn','status':'completed'}}})
 else:send({'id':r['id'],'result':responses[m]})
""")
    journal = Journal(tmp_path / "evidence", b"PRIVATE SOURCE")
    try:
        with codex.app_server(
            [
                sys.executable,
                str(cli),
                json.dumps(Transcript().responses),
                str(calls),
                json.dumps(completed()[1]),
                fault,
            ],
            lambda: False,
            time.monotonic() + 0.4,
        ) as rpc:
            rpc.evidence = journal.record
            with pytest.raises(codex.Hold) as held:
                codex.turn(rpc, "fixture-model", None, "private prompt", {})
            assert rpc.lifecycle["thread_id"] == "thread" and rpc.lifecycle["turn_id"] == "turn"
            if fault == "disconnect":
                assert not held.value.terminal and held.value.counted is None
            else:
                assert held.value.terminal and held.value.counted["total_tokens"] == 24
    finally:
        journal.close()
    methods = [json.loads(x) for x in calls.read_text().splitlines()]
    assert methods.count("turn/start") == 1
    if fault == "malformed-complete":
        assert "turn/interrupt" not in methods
    events = [json.loads(x) for x in journal.path.read_text().splitlines()]
    assert any(e["phase"] == "lifecycle-turn" and e["turn_id"] == "turn" for e in events)
    if fault != "disconnect":
        assert any(e["phase"] == "lifecycle-terminal" for e in events)
    assert (
        "PRIVATE SOURCE" not in journal.path.read_text()
        and "private prompt" not in journal.path.read_text()
    )


def test_foreign_or_malformed_lifecycle_cannot_settle_an_owned_turn(tmp_path):
    import os

    read, write = os.pipe()
    channel = codex.Channel(read, lambda _: None, lambda: False, time.monotonic() + 1)
    channel.lifecycle = dict(thread_id="owned-thread", turn_id="owned-turn")
    try:
        for params in [
            dict(threadId="foreign", turn=dict(id="owned-turn", status="completed")),
            dict(threadId="owned-thread", turn=dict(id="foreign", status="completed")),
            dict(turn=None),
            dict(turn="malformed"),
        ]:
            channel.observe(dict(method="turn/completed", params=params))
        assert "terminal_status" not in channel.lifecycle
    finally:
        channel.close()
        os.close(read)
        os.close(write)


def test_persistent_rollout_mount_never_shares_owner_home_or_credential_directory(tmp_path):
    sessions = tmp_path / "per-request-sessions"
    sessions.mkdir(mode=0o700)
    argv = codex.command("/pinned-cli", "/private/auth.json", sessions=sessions)
    assert argv[argv.index(str(sessions)) - 1 : argv.index(str(sessions)) + 2] == [
        "--bind",
        str(sessions),
        "/codex/sessions",
    ]
    assert argv[argv.index("/private/auth.json") - 1 : argv.index("/private/auth.json") + 2] == [
        "--ro-bind",
        "/private/auth.json",
        "/codex/auth.json",
    ]
    assert "/home/rich" not in argv and "/private" not in argv and "--clearenv" in argv
    sessions.chmod(0o755)
    with pytest.raises(ValueError):
        codex.command("/pinned-cli", "/private/auth.json", sessions=sessions)


@pytest.mark.parametrize("returned_id", ["owned-turn", "foreign-turn"])
def test_supported_status_read_accepts_only_the_same_turn_without_generation(returned_id):
    import os

    read, write = os.pipe()
    sent = []
    rpc = codex.Channel(
        read, lambda data: sent.append(json.loads(data)), lambda: True, time.monotonic() - 1
    )
    rpc.lifecycle = dict(thread_id="owned-thread", turn_id="owned-turn")
    try:
        os.write(
            write,
            (
                json.dumps(dict(id=1, result=dict(data=[dict(id=returned_id, status="completed")])))
                + "\n"
            ).encode(),
        )
        status, counted = rpc.retrieve_status("owned-thread", "owned-turn")
        assert status == ("completed" if returned_id == "owned-turn" else None)
        assert counted is None and rpc.stop()
        assert [x["method"] for x in sent] == ["thread/turns/list"]
    finally:
        rpc.close()
        os.close(read)
        os.close(write)
