from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import uuid

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtNetwork import QLocalSocket
from PySide6.QtWidgets import QApplication

from endfield_pcap import overlay_control as control
from endfield_pcap.overlay_window_config import OverlayWindowConfig
from endfield_pcap.rotation_client import OverlaySettingsStore


def _app():
    global _application
    _application = QCoreApplication.instance() or QApplication([])
    return _application


def _until(predicate, timeout=3):
    app = _app(); deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents(); time.sleep(.005)
    assert predicate()


def _finish(*objects):
    for obj in objects: obj.close()
    _app().processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


class Manager:
    def __init__(self, root):
        self.config = OverlayWindowConfig(root / "windows.json")
        self.diagnostics = SimpleNamespace(text=lambda identifier: "本窗口 TypeError 第 12 行")
        self.reloaded = []

    def list_entries(self): return self.config.list_entries()
    def get_entry(self, identifier): return self.config.get_entry(identifier)
    def add_entry(self, *args): return self.config.add_entry(*args)
    def update_entry(self, identifier, **patch): return self.config.update_entry(identifier, **patch)
    def remove_entry(self, identifier): return self.config.remove_entry(identifier)
    def reload_entry(self, identifier): self.reloaded.append(identifier)
    def recover_entry(self, identifier): return self.config.update_entry(identifier, geometry={"x":100,"y":100,"width":440,"height":320})


class Rotation:
    def __init__(self, store): self.settings = store.load(); self.store=store
    def apply_module_settings(self, module, *, enabled, opacity):
        assert module == "meter"
        self.settings.update(meterEnabled=enabled, meterOpacity=opacity)
        self.store.save(self.settings)


def test_same_user_private_pipe_controls_host_and_never_uses_offline_config(tmp_path):
    _app()
    host_root = tmp_path / "host"; local_root=tmp_path/"uploader"
    html=tmp_path/"monitor.html";html.write_text("<h1>test</h1>",encoding="utf-8")
    meter=OverlaySettingsStore(tmp_path/"meter.json")
    manager=Manager(host_root);rotation=Rotation(meter)
    name="endfield-test-"+uuid.uuid4().hex;token="a"*64
    server=control.OverlayControlServer(manager,rotation,name=name,token=token)
    client=control.OverlayControlClient(name=name,token=token,root=local_root,meter_store=meter)
    finished={};failed=[]
    client.operationFinished.connect(lambda identifier,result:finished.__setitem__(identifier,result))
    client.operationFailed.connect(lambda identifier,message:failed.append((identifier,message)))
    try:
        _until(lambda:client.snapshot["online"])
        request=client.submit("add",{"name":"监控","sourceType":"file","sourceValue":str(html)})
        _until(lambda:request in finished)
        entry=finished[request]["value"];identifier=entry["id"]
        assert client.list_entries()==manager.list_entries() and not (local_root/"windows.json").exists()
        # The uploader's cached position may be stale after a live drag. Only its
        # requested dimension is merged into the current host geometry.
        manager.update_entry(identifier,geometry={"x":777,"y":888,"width":440,"height":320})
        request=client.submit("update",{"id":identifier,"patch":{"geometry":{"width":660}}})
        _until(lambda:request in finished)
        assert manager.get_entry(identifier)["geometry"]=={"x":777,"y":888,"width":660,"height":320}
        request=client.submit("meter",{"enabled":False,"opacity":.65})
        _until(lambda:request in finished)
        assert rotation.settings["meterEnabled"] is False and rotation.settings["meterOpacity"]==.65
        request=client.submit("diagnostics",{"id":identifier})
        _until(lambda:request in finished)
        assert "TypeError" in finished[request]["value"]
        opened=[];client.openSettingsRequested.connect(lambda:opened.append(True))
        assert server.request_settings()
        _until(lambda:bool(opened))
        assert not failed
    finally:_finish(client,server)


def test_offline_configuration_is_explicit_and_only_applies_next_start(tmp_path):
    _app()
    root=tmp_path/"offline";meter=OverlaySettingsStore(tmp_path/"meter.json")
    client=control.OverlayControlClient(name="",token="",root=root,meter_store=meter)
    completed={};failures=[]
    client.operationFinished.connect(lambda identifier,result:completed.__setitem__(identifier,result))
    client.operationFailed.connect(lambda identifier,message:failures.append((identifier,message)))
    html=tmp_path/"local.html";html.write_text("<div>local</div>",encoding="utf-8")
    try:
        request=client.submit("add",{"name":"离线配置","sourceType":"file","sourceValue":str(html)})
        assert request not in completed
        _until(lambda:request in completed)
        entry=completed[request]["value"]
        assert completed[request]["offlineSaved"] is True and client.snapshot["online"] is False
        assert client.snapshot["capabilities"]=={"configuration":True,"liveActions":False}
        request=client.submit("meter",{"enabled":False,"opacity":.7})
        _until(lambda:request in completed)
        assert meter.load()["meterEnabled"] is False and meter.load()["meterOpacity"]==.7
        request=client.submit("reload",{"id":entry["id"]})
        _until(lambda:any(row[0]==request for row in failures))
        assert "需要运行中" in failures[-1][1]
    finally:_finish(client)


def test_managed_connection_unavailable_never_falls_back_to_writing_local_settings(tmp_path):
    _app();root=tmp_path/"no-service"
    client=control.OverlayControlClient(name="endfield-missing-"+uuid.uuid4().hex,token="b"*64,
                                        root=root,meter_store=OverlaySettingsStore(tmp_path/"meter.json"))
    failures=[];client.operationFailed.connect(lambda identifier,message:failures.append((identifier,message)))
    html=tmp_path/"local.html";html.write_text("test",encoding="utf-8")
    try:
        request=client.submit("add",{"name":"不可伪成功","sourceType":"file","sourceValue":str(html)})
        _until(lambda:any(row[0]==request for row in failures))
        assert not (root/"windows.json").exists() and not client.snapshot["capabilities"]["configuration"]
    finally:_finish(client)


def test_invalid_capability_and_unlisted_operation_cannot_mutate_host(tmp_path):
    _app();manager=Manager(tmp_path/"host");rotation=Rotation(OverlaySettingsStore(tmp_path/"meter.json"))
    name="endfield-auth-"+uuid.uuid4().hex
    server=control.OverlayControlServer(manager,rotation,name=name,token="a"*64)
    bad=QLocalSocket();bad.connectToServer(name)
    try:
        _until(lambda:bad.state()==QLocalSocket.LocalSocketState.ConnectedState)
        bad.write((json.dumps({"id":uuid.uuid4().hex,"token":"b"*64,"operation":"meter","arguments":{"enabled":False,"opacity":.5}})+"\n").encode())
        _until(lambda:bad.state()==QLocalSocket.LocalSocketState.UnconnectedState)
        assert rotation.settings["meterEnabled"] is True
        assert server.server.socketOptions()==control.QLocalServer.SocketOption.UserAccessOption
        with pytest.raises(ValueError):control._arguments("execute",{"code":"process"})
    finally:bad.abort();_finish(server)


def test_disconnect_rejects_pending_mutation_and_never_writes_offline(tmp_path):
    _app();manager=Manager(tmp_path/"host");rotation=Rotation(OverlaySettingsStore(tmp_path/"meter.json"))
    name="endfield-disconnect-"+uuid.uuid4().hex;token="a"*64
    server=control.OverlayControlServer(manager,rotation,name=name,token=token)
    local=tmp_path/"uploader"
    client=control.OverlayControlClient(name=name,token=token,root=local,meter_store=rotation.store)
    failures=[];client.operationFailed.connect(lambda identifier,message:failures.append((identifier,message)))
    try:
        _until(lambda:client.snapshot["online"])
        request=client.submit("meter",{"enabled":False,"opacity":.5})
        server.close()
        _until(lambda:any(row[0]==request for row in failures))
        assert client.snapshot["online"] is False and not client.snapshot["capabilities"]["configuration"]
        assert rotation.settings["meterEnabled"] is True and not (local/"windows.json").exists()
    finally:_finish(client,server)


def test_response_supports_all_24_entries_without_unbounded_frames(tmp_path):
    _app();manager=Manager(tmp_path/"host");rotation=Rotation(OverlaySettingsStore(tmp_path/"meter.json"))
    for index in range(24):
        manager.add_entry("窗口"+str(index),"url","https://example.com/?value="+"a"*4000)
    name="endfield-limit-"+uuid.uuid4().hex;token="a"*64
    server=control.OverlayControlServer(manager,rotation,name=name,token=token)
    client=control.OverlayControlClient(name=name,token=token,root=tmp_path/"uploader",meter_store=rotation.store)
    try:
        _until(lambda:client.snapshot["online"])
        assert len(client.list_entries())==24
        assert len(json.dumps(client.snapshot).encode())>control.MAX_REQUEST_BYTES
        bad=QLocalSocket();bad.connectToServer(name)
        _until(lambda:bad.state()==QLocalSocket.LocalSocketState.ConnectedState)
        bad.write(b"x"*(control.MAX_REQUEST_BYTES+1))
        _until(lambda:bad.state()==QLocalSocket.LocalSocketState.UnconnectedState)
        assert len(manager.list_entries())==24
        bad.abort()
    finally:_finish(client,server)


def test_offline_submission_queue_is_bounded(tmp_path):
    _app();client=control.OverlayControlClient(name="",token="",root=tmp_path,meter_store=OverlaySettingsStore(tmp_path/"meter.json"))
    try:
        for _ in range(control.MAX_PENDING):client.submit("snapshot")
        with pytest.raises(RuntimeError,match="正在处理"):client.submit("snapshot")
        _until(lambda:not client._pending)
    finally:_finish(client)


def test_actual_separate_process_pipe_applies_meter_settings(tmp_path):
    _app()
    name="endfield-process-"+uuid.uuid4().hex;token="c"*64
    environment=dict(os.environ)
    environment[control.NAME_ENV]=name;environment[control.TOKEN_ENV]=token
    environment["PYTHONDONTWRITEBYTECODE"]="1"
    with (tmp_path/"host.log").open("wb") as log:
        process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),"--control-test-host",str(tmp_path)],
                                 env=environment,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
                                 creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        client=None
        try:
            _until(lambda:(tmp_path/"ready").exists() or process.poll() is not None,timeout=10)
            assert process.poll() is None,(tmp_path/"host.log").read_text(encoding="utf-8",errors="replace")
            client=control.OverlayControlClient(name=name,token=token,root=tmp_path/"unused-ui-config",meter_store=OverlaySettingsStore(tmp_path/"unused-meter.json"))
            finished={};client.operationFinished.connect(lambda identifier,result:finished.__setitem__(identifier,result))
            _until(lambda:client.snapshot["online"])
            request=client.submit("meter",{"enabled":False,"opacity":.55})
            _until(lambda:request in finished)
            assert finished[request]["snapshot"]["meter"]["meterEnabled"] is False
            assert json.loads((tmp_path/"meter.json").read_text(encoding="utf-8"))["meterOpacity"]==.55
            assert not (tmp_path/"unused-meter.json").exists()
        finally:
            if client is not None:_finish(client)
            (tmp_path/"stop").write_text("stop",encoding="utf-8")
            process.wait(timeout=10)


def test_no_environment_offline_writer_cannot_overwrite_running_host(tmp_path):
    _app()
    name="endfield-offline-race-"+uuid.uuid4().hex;token="d"*64
    environment=dict(os.environ)
    environment[control.NAME_ENV]=name;environment[control.TOKEN_ENV]=token
    environment["PYTHONDONTWRITEBYTECODE"]="1"
    with (tmp_path/"host.log").open("wb") as log:
        process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),"--control-test-host",str(tmp_path)],
                                 env=environment,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
                                 creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        offline=None
        try:
            _until(lambda:(tmp_path/"ready").exists() or process.poll() is not None,timeout=10)
            assert process.poll() is None
            root=tmp_path/"host";meter=OverlaySettingsStore(tmp_path/"meter.json")
            offline=control.OverlayControlClient(name="",token="",root=root,meter_store=meter)
            failures=[];completed={}
            offline.operationFailed.connect(lambda identifier,message:failures.append((identifier,message)))
            offline.operationFinished.connect(lambda identifier,result:completed.__setitem__(identifier,result))
            assert offline.snapshot["capabilities"]["configuration"] is False
            request=offline.submit("meter",{"enabled":False,"opacity":.65})
            _until(lambda:any(row[0]==request for row in failures))
            assert not (tmp_path/"meter.json").exists()
            (tmp_path/"stop").write_text("stop",encoding="utf-8")
            process.wait(timeout=10)
            # A released host lease permits a later offline save; no runtime
            # state is copied from the uploader's stale initial snapshot.
            request=offline.submit("meter",{"enabled":False,"opacity":.65})
            _until(lambda:request in completed)
            assert completed[request]["offlineSaved"] is True
            assert meter.load()["meterOpacity"]==.65
        finally:
            if offline is not None:_finish(offline)
            if process.poll() is None:
                (tmp_path/"stop").write_text("stop",encoding="utf-8")
                process.wait(timeout=10)


def test_offline_writer_rechecks_host_lease_after_page_was_opened(tmp_path):
    _app();root=tmp_path/"shared";meter=OverlaySettingsStore(tmp_path/"meter.json")
    offline=control.OverlayControlClient(name="",token="",root=root,meter_store=meter)
    failures=[];offline.operationFailed.connect(lambda identifier,message:failures.append((identifier,message)))
    assert offline.snapshot["capabilities"]["configuration"]
    lease=control.OverlayConfigLease(root);assert lease.held
    try:
        request=offline.submit("meter",{"enabled":False,"opacity":.5})
        _until(lambda:any(row[0]==request for row in failures))
        assert not meter.path.exists()
    finally:lease.close();_finish(offline)


def test_abrupt_host_exit_does_not_leave_configuration_permanently_locked(tmp_path):
    _app()
    name="endfield-crash-lease-"+uuid.uuid4().hex;token="e"*64
    environment=dict(os.environ);environment[control.NAME_ENV]=name;environment[control.TOKEN_ENV]=token
    with (tmp_path/"host.log").open("wb") as log:
        process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),"--control-test-host",str(tmp_path)],
                                 env=environment,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
                                 creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        try:
            _until(lambda:(tmp_path/"ready").exists() or process.poll() is not None,timeout=10)
            assert process.poll() is None
            (tmp_path/"crash").write_text("crash",encoding="utf-8")
            process.wait(timeout=10)
            lease=control.OverlayConfigLease(tmp_path/"host")
            try:assert lease.held
            finally:lease.close()
        finally:
            if process.poll() is None:
                (tmp_path/"stop").write_text("stop",encoding="utf-8");process.wait(timeout=10)


@pytest.mark.parametrize("arguments",[{"enabled":True,"opacity":float("nan")},{"enabled":1,"opacity":.5},
                                      {"enabled":False,"opacity":1.1},{"enabled":False,"opacity":.5,"guide":True}])
def test_meter_control_rejects_untrusted_parameters(arguments):
    with pytest.raises(ValueError):control._meter_arguments(arguments)


if __name__=="__main__" and sys.argv[1:2]==["--control-test-host"]:
    app=_app();root=Path(sys.argv[2])
    manager=Manager(root/"host");rotation=Rotation(OverlaySettingsStore(root/"meter.json"))
    server=control.OverlayControlServer(manager,rotation,name=os.environ[control.NAME_ENV],token=os.environ[control.TOKEN_ENV])
    timer=QTimer()
    def stop_requested():
        if (root/"crash").exists():
            os._exit(0)  # This test-owned child alone exits without Qt cleanup.
        if (root/"stop").exists():
            server.close()
            app.quit()
    timer.timeout.connect(stop_requested)
    timer.start(25)
    (root/"ready").write_text("ready",encoding="utf-8")
    raise SystemExit(app.exec())
