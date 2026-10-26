import zipfile

import pack_release


def test_runtime_component_zip_is_stable_without_changing_module_bytes(tmp_path):
    outputs = []
    contents = {"encodings/__init__.pyc": b"compiled-encoding", "abc.pyc": b"compiled-abc"}
    for index, order in enumerate((list(contents), list(reversed(contents)))):
        dist = tmp_path / str(index)
        dist.mkdir()
        path = dist / "base_library.zip"
        with zipfile.ZipFile(path, "w") as archive:
            for name in order:
                info = zipfile.ZipInfo(name, date_time=(2026, 10, 20 + index, 10, 0, 0))
                archive.writestr(info, contents[name])
        pack_release._normalize_runtime_archives(dist)
        outputs.append(path.read_bytes())
        with zipfile.ZipFile(path) as archive:
            assert {name:archive.read(name) for name in archive.namelist()} == contents
    assert outputs[0] == outputs[1]
