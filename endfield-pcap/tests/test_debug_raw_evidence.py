import base64
import hashlib
from types import SimpleNamespace

from google.protobuf.empty_pb2 import Empty

from endfield_pcap.message_registry import DecodedMessage
from endfield_pcap.service import DamageLogService


def test_debug_export_preserves_large_unknown_protobuf_fields(tmp_path):
    # Empty has no defined fields. A future length-delimited field must still
    # survive debug export, even beyond the former 4/8 KiB prefix limits.
    data = b'x' * 20000
    size, varint = len(data), bytearray()
    while size > 127:
        varint.append((size & 127) | 128)
        size >>= 7
    varint.append(size)
    wire = b'\x0a' + bytes(varint) + data
    message = Empty.FromString(wire)
    decoded = DecodedMessage('sc', 'TEST_FUTURE', 1, {'msgid': 1}, message, wire)
    service = DamageLogService.__new__(DamageLogService)
    service.debug_session_dir = tmp_path
    service.active_session = SimpleNamespace(session_id='test-session')
    saved = []
    service._write_debug_json = lambda *args: saved.append(args[2])
    service._handle_debug_message(decoded, 123)
    payload = saved[0]
    assert payload['message'] == {}
    assert base64.b64decode(payload['raw_body_base64']) == wire
    assert payload['raw_body_size'] == len(wire)
    assert payload['raw_body_sha256'] == hashlib.sha256(wire).hexdigest()
    assert payload['raw_body_format'] == 'protobuf_body_after_decompression_and_reassembly'
    assert payload['protobuf_schema_files_sha256']['google/protobuf/empty.proto'] == hashlib.sha256(
        message.DESCRIPTOR.file.serialized_pb).hexdigest()


def test_debug_export_does_not_write_without_a_debug_session():
    service = DamageLogService.__new__(DamageLogService)
    service.debug_session_dir = None
    service.active_session = SimpleNamespace(session_id='test-session')
    service._write_debug_json = lambda *args: (_ for _ in ()).throw(AssertionError('unexpected write'))
    service._handle_debug_message(DecodedMessage('sc', 'TEST', 1, {}, Empty(), b''), 123)
