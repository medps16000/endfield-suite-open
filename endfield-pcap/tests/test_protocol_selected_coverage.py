from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from google.protobuf import descriptor_pb2
from google.protobuf.descriptor import FileDescriptor
from grpc_tools import protoc

from endfield_pcap.message_registry import MessageRegistry


ROOT = Path(__file__).resolve().parents[1]
SERVER_SKILL_MESSAGES = (
    (1175, "SC_ATTACH_SERVER_SKILL"),
    (1176, "SC_DETACH_SERVER_SKILL"),
    (1180, "SC_UPDATE_SERVER_SKILL"),
)


def _selected_messages() -> list[tuple[str, int, str]]:
    registry = MessageRegistry(ROOT / "data")
    return [
        (direction, msg_id, registry.resolve_class_name(direction, msg_id))
        for direction, msg_ids in registry._interesting_msg_ids.items()
        for msg_id in sorted(msg_ids)
    ]


@pytest.fixture(scope="module")
def registry() -> MessageRegistry:
    return MessageRegistry(ROOT / "data")


@pytest.mark.parametrize("direction,msg_id,class_name", _selected_messages())
def test_selected_id_loads_its_exact_descriptor(
    registry: MessageRegistry, direction: str, msg_id: int, class_name: str
) -> None:
    assert registry.should_decode_message(direction, msg_id)
    assert registry.resolve_class_name(direction, msg_id) == class_name
    message_class = registry.load_message_class(class_name)
    assert message_class is not None, (direction, msg_id, class_name)
    assert message_class.DESCRIPTOR.name == class_name
    assert message_class.DESCRIPTOR.full_name == class_name
    assert message_class.DESCRIPTOR.file.name == f"{class_name}.proto"
    assert message_class.DESCRIPTOR.file.message_types_by_name[class_name] is message_class.DESCRIPTOR


def test_selected_recursive_dependency_modules_are_available(registry: MessageRegistry) -> None:
    pending = []
    for _, _, class_name in _selected_messages():
        message_class = registry.load_message_class(class_name)
        assert message_class is not None
        pending.append(message_class.DESCRIPTOR.file)

    checked = set()
    while pending:
        file = pending.pop()
        if file.name in checked:
            continue
        checked.add(file.name)
        module = importlib.import_module(
            f"endfield_pcap.proto_generated.{Path(file.name).stem}_pb2"
        )
        assert module.DESCRIPTOR is file, file.name
        schema = descriptor_pb2.FileDescriptorProto.FromString(file.serialized_pb)
        assert schema.name == file.name
        assert list(schema.dependency) == [dependency.name for dependency in file.dependencies]
        pending.extend(file.dependencies)


def _wire_file_descriptor(file: descriptor_pb2.FileDescriptorProto) -> descriptor_pb2.FileDescriptorProto:
    result = descriptor_pb2.FileDescriptorProto()
    result.CopyFrom(file)
    pending = list(result.message_type)
    while pending:
        message = pending.pop()
        for field in [*message.field, *message.extension]:
            # protoc's descriptor-set output includes inferred JSON names while
            # generated AddSerializedFile bytes omit them. They are not wire fields.
            field.ClearField("json_name")
        pending.extend(message.nested_type)
    for field in result.extension:
        field.ClearField("json_name")
    result.ClearField("source_code_info")
    return result


def test_server_skill_modules_match_source_and_recursive_wire_schema(
    registry: MessageRegistry, tmp_path: Path
) -> None:
    descriptor_path = tmp_path / "server-skill-descriptors.pb"
    proto_names = [f"{name}.proto" for _, name in SERVER_SKILL_MESSAGES]
    assert protoc.main(
        [
            "protoc",
            f"-I{ROOT / 'proto'}",
            f"--descriptor_set_out={descriptor_path}",
            "--include_imports",
            *proto_names,
        ]
    ) == 0
    sources = descriptor_pb2.FileDescriptorSet.FromString(descriptor_path.read_bytes())

    pending: list[FileDescriptor] = []
    for msg_id, class_name in SERVER_SKILL_MESSAGES:
        assert registry.resolve_class_name("sc", msg_id) == class_name
        message_class = registry.load_message_class(class_name)
        assert message_class is not None
        pending.append(message_class.DESCRIPTOR.file)
    runtime = {}
    while pending:
        file = pending.pop()
        if file.name in runtime:
            continue
        runtime[file.name] = descriptor_pb2.FileDescriptorProto.FromString(file.serialized_pb)
        pending.extend(file.dependencies)

    assert set(runtime) == {source.name for source in sources.file}
    for source in sources.file:
        actual = _wire_file_descriptor(runtime[source.name])
        expected = _wire_file_descriptor(source)
        if source.name == "BATTLE_ACTION_OWNER_TYPE.proto":
            # The existing generated dependency prefixes this single zero-value
            # name to avoid the source files' global enum-name collision.
            # Keep the exception exact; enum numbers and every other field must match.
            assert expected.enum_type[0].name == "BATTLE_ACTION_OWNER_TYPE"
            assert (expected.enum_type[0].value[0].name, expected.enum_type[0].value[0].number) == (
                "None", 0
            )
            assert (actual.enum_type[0].value[0].name, actual.enum_type[0].value[0].number) == (
                "BATTLE_ACTION_OWNER_TYPE_None", 0
            )
            expected.enum_type[0].value[0].name = "BATTLE_ACTION_OWNER_TYPE_None"
        assert actual == expected, source.name


@pytest.mark.parametrize("msg_id,class_name", SERVER_SKILL_MESSAGES)
def test_server_skill_wire_round_trip_through_registry(
    registry: MessageRegistry, msg_id: int, class_name: str
) -> None:
    message_class = registry.load_message_class(class_name)
    assert message_class is not None
    message = message_class(inst_id=2**53 + 27, generation=9)
    skill_inst_id = 2**61 + 101
    if class_name == "SC_DETACH_SERVER_SKILL":
        message.del_inst_ids.extend([skill_inst_id, skill_inst_id + 1])
    else:
        skill = message.skills.add(
            inst_id=skill_inst_id,
            potential_lv=2,
            level=5,
            source=2,
            talent_node_ids=["protocol-round-trip"],
            is_server_init=True,
            is_enable=True,
            is_passive=True,
        )
        skill.skill_id.int_id = 2665
        skill.skill_id.type = 2
        skill.blackboard.blackboard["max_stack"].float_value = 16.0
        skill.blackboard.blackboard["max_stack"].is_dynamic = True
        skill.blackboard.blackboard["label"].str_value = "protocol-round-trip"

    encoded = message.SerializeToString(deterministic=True)
    assert encoded
    decoded = registry.decode_messages("sc", {"msgid": msg_id}, encoded)
    assert len(decoded) == 1
    assert decoded[0].class_name == class_name
    assert decoded[0].msg_id == msg_id
    assert decoded[0].raw_body == encoded
    assert decoded[0].message == message
    assert decoded[0].message.inst_id == 2**53 + 27
    assert decoded[0].message.generation == 9
    if class_name == "SC_DETACH_SERVER_SKILL":
        assert list(decoded[0].message.del_inst_ids) == [skill_inst_id, skill_inst_id + 1]
    else:
        actual_skill = decoded[0].message.skills[0]
        assert actual_skill.inst_id == skill_inst_id
        assert actual_skill.skill_id.WhichOneof("id_impl") == "int_id"
        assert actual_skill.skill_id.int_id == 2665
        assert actual_skill.blackboard.blackboard["max_stack"].float_value == 16.0
        assert actual_skill.blackboard.blackboard["max_stack"].is_dynamic
        assert actual_skill.blackboard.blackboard["label"].str_value == "protocol-round-trip"
