import asyncio
from collections import defaultdict, deque

from endfield_pcap.flow import TcpStreamReassembler
from endfield_pcap.models import CapturedPacket, Endpoint, FlowKey, RuntimeMetrics, ServiceState
from endfield_pcap.service import DamageLogService, SessionPipeline


def _service_shell() -> DamageLogService:
    service = DamageLogService.__new__(DamageLogService)
    service.pending_packets = defaultdict(lambda: deque(maxlen=8192))
    return service


def test_packet_backlog_yields_to_status_tasks_without_reordering():
    async def exercise():
        service = _service_shell()
        flow = FlowKey(client=Endpoint('127.0.0.1', 50000), server=Endpoint('127.0.0.2', 30000))
        service.state = ServiceState.LIVE
        service.active_flow = flow
        processed = []
        service.active_session = type('Session', (), {
            'is_live': False,
            'process_packet': lambda self, packet: processed.append(packet.seq),
        })()
        service.packet_queue = asyncio.Queue()
        for number in range(100):
            service.packet_queue.put_nowait(CapturedPacket(
                timestamp_ms=number, src=flow.client, dst=flow.server,
                seq=number, payload=b'x', device_name='fixture'))
        task = asyncio.create_task(service._packet_loop())
        try:
            await asyncio.sleep(0)
            assert 0 < len(processed) < 100
            while len(processed) < 100:
                await asyncio.sleep(0)
            assert processed == list(range(100))
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(exercise())


def test_flow_activation_falls_back_to_process_connection_without_buffered_packets() -> None:
    service = _service_shell()
    flow = FlowKey(
        client=Endpoint("192.168.0.105", 57874),
        server=Endpoint("47.123.190.224", 30000),
    )

    assert service._ready_to_activate_flow(flow) is False
    assert service._flow_activation_reason(flow) == "process_connection_fallback"


def test_flow_activation_keeps_buffered_bidirectional_reason_when_available() -> None:
    service = _service_shell()
    flow = FlowKey(
        client=Endpoint("192.168.0.105", 57874),
        server=Endpoint("47.123.190.224", 30000),
    )
    service.pending_packets[flow].append(
        CapturedPacket(
            timestamp_ms=1,
            src=flow.client,
            dst=flow.server,
            seq=100,
            payload=b"client",
            device_name="device8",
        )
    )
    service.pending_packets[flow].append(
        CapturedPacket(
            timestamp_ms=2,
            src=flow.server,
            dst=flow.client,
            seq=200,
            payload=b"server",
            device_name="device8",
        )
    )

    assert service._ready_to_activate_flow(flow) is True
    assert service._flow_activation_reason(flow) == "buffered_bidirectional_payload"


def test_live_flow_keeps_existing_capture_handles_to_preserve_tcp_stream() -> None:
    class SessionSpy:
        is_live = True

    class CaptureManagerSpy:
        def __init__(self) -> None:
            self.lock_calls = 0

        def lock_to_flow(self, flow, observed_device_names) -> None:
            self.lock_calls += 1

    service = _service_shell()
    service.active_flow = FlowKey(
        client=Endpoint("192.168.0.105", 57874),
        server=Endpoint("47.123.190.224", 30000),
    )
    service.active_session = SessionSpy()
    service.capture_manager = CaptureManagerSpy()
    service._active_flow_capture_locked = False

    service._lock_active_flow_capture_if_ready()

    assert service._active_flow_capture_locked is True
    assert service.capture_manager.lock_calls == 0


def test_tcp_gap_is_reported_without_debug_capture_enabled() -> None:
    flow = FlowKey(
        client=Endpoint("192.168.0.105", 57874),
        server=Endpoint("47.123.190.224", 30000),
    )
    pipeline = SessionPipeline.__new__(SessionPipeline)
    pipeline.flow = flow
    pipeline.session_id = "test-session"
    pipeline.client_reassembler = TcpStreamReassembler()
    pipeline.server_reassembler = TcpStreamReassembler()
    pipeline.client_buffer = bytearray()
    pipeline.server_buffer = bytearray()
    pipeline._last_gap_report = {}
    pipeline.first_packet_ts_ms = None
    pipeline.startup_tcp_gap_count = 0
    pipeline.startup_tcp_gap_max_missing_bytes = 0
    pipeline.reliability_flags = set()
    pipeline._startup_gap_warning_emitted = False
    pipeline.metrics = RuntimeMetrics()
    pipeline.on_debug_record = None
    emitted = []
    pipeline.on_event = emitted.append

    pipeline.process_packet(
        CapturedPacket(
            timestamp_ms=1_000,
            src=flow.server,
            dst=flow.client,
            seq=100,
            payload=b"\x00",
            device_name="device8",
        )
    )
    pipeline.process_packet(
        CapturedPacket(
            timestamp_ms=1_100,
            src=flow.server,
            dst=flow.client,
            seq=103,
            payload=b"\x00",
            device_name="device8",
        )
    )

    assert pipeline._last_gap_report["sc"] == (101, 103, 2)
    assert emitted[-1].event_type == "SESSION_WARNING"


def test_reset_session_seals_trace_once_before_clearing_capture_state() -> None:
    class TraceBridgeSpy:
        def __init__(self) -> None:
            self.end_calls = 0

        def end_capture_session(self) -> None:
            self.end_calls += 1

    class SessionSpy:
        def __init__(self) -> None:
            self.flush_calls = 0

        def flush_debug_state(self) -> None:
            self.flush_calls += 1

    class CaptureManagerSpy:
        def __init__(self) -> None:
            self.restore_calls = 0

        def restore_default_filters(self) -> None:
            self.restore_calls += 1

    service = _service_shell()
    service.trace_bridge = TraceBridgeSpy()
    service.active_flow = object()
    service.active_session = SessionSpy()
    session = service.active_session
    service._active_flow_capture_locked = True
    service.capture_manager = CaptureManagerSpy()
    service.log_file = None
    service.current_log_path = object()
    service.debug_session_dir = object()
    service.debug_counters = defaultdict(int)

    service._reset_session()
    service._reset_session()

    assert service.trace_bridge.end_calls == 1
    assert session.flush_calls == 1
    assert service.active_flow is None
    assert service.active_session is None
    assert service._active_flow_capture_locked is False
    assert service.current_log_path is None
    assert service.debug_session_dir is None


def test_background_task_failure_is_written_to_status_before_service_stops() -> None:
    class TaskSpy:
        def exception(self):
            return AttributeError("FieldDescriptor.label is unavailable")

        def get_name(self) -> str:
            return "packet-loop"

    class StopEventSpy:
        def __init__(self) -> None:
            self.was_set = False

        def is_set(self) -> bool:
            return self.was_set

        def set(self) -> None:
            self.was_set = True

    class LoopSpy:
        def is_closed(self) -> bool:
            return False

        def call_soon_threadsafe(self, callback) -> None:
            callback()

    service = _service_shell()
    service._fatal_exception = None
    service._fatal_error = None
    service.loop = LoopSpy()
    service._stop_event = StopEventSpy()
    written_statuses: list[dict[str, str]] = []
    service._write_status = lambda: written_statuses.append(dict(service._fatal_error or {}))

    service._on_background_task_done(TaskSpy())

    assert written_statuses == [
        {
            "task": "packet-loop",
            "type": "AttributeError",
            "message": "FieldDescriptor.label is unavailable",
        }
    ]
    assert service._stop_event.was_set is True
