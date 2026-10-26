from types import SimpleNamespace

import pytest

from infra.scripts.verify_native_damage_evidence import read_native_bytes


class SectionImage:
    """Minimal PE section with deliberately different RVA and raw offsets."""
    OPTIONAL_HEADER = SimpleNamespace(ImageBase=0x180000000)

    def __init__(self):
        self.__data__ = b'\0' * 0x200 + b'\x48\x89\x5c\x24\x08' + b'\x90' * 32
        self.requested_rvas = []

    def get_offset_from_rva(self, rva):
        if rva < 0x1000:
            raise ValueError('RVA was replaced by a raw file offset')
        return rva - 0x1000 + 0x200

    def get_data(self, rva, size):
        self.requested_rvas.append(rva)
        offset = self.get_offset_from_rva(rva)
        return self.__data__[offset:offset + size]


def test_native_reader_does_not_apply_rva_translation_twice():
    pe = SectionImage()
    assert read_native_bytes(pe, 0x180001000, 5) == bytes.fromhex('48895c2408')
    assert pe.requested_rvas == [0x1000]
    with pytest.raises(ValueError):
        pe.get_data(pe.get_offset_from_rva(0x1000), 5)


def test_native_reader_rejects_different_bytes_or_truncation():
    pe = SectionImage()
    pe.get_data = lambda rva, size: b'\xff' * size
    with pytest.raises(ValueError, match='read mismatch'):
        read_native_bytes(pe, 0x180001000, 5)
    pe = SectionImage()
    with pytest.raises(ValueError, match='read mismatch'):
        read_native_bytes(pe, 0x180001000, 100)
