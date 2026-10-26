from __future__ import annotations


class TcpStreamReassembler:
    def __init__(self) -> None:
        self._segments: dict[int, bytes] = {}
        self._next_seq: int | None = None
        self.observer_overlap_conflicts = 0

    def reset(self) -> None:
        self._segments.clear()
        self._next_seq = None

    def gap_state(self) -> tuple[int, int, int] | None:
        if self._next_seq is None or not self._segments:
            return None
        lowest_seq = min(self._segments)
        if lowest_seq <= self._next_seq:
            return None
        return (self._next_seq, lowest_seq, lowest_seq - self._next_seq)

    def accept(self, seq: int, payload: bytes) -> list[bytes]:
        if not payload:
            return []

        if self._next_seq is None:
            self._next_seq = seq

        if self._next_seq is None:
            return []

        end_seq = seq + len(payload)
        if end_seq <= self._next_seq:
            return []

        if seq < self._next_seq:
            payload = payload[self._next_seq - seq :]
            seq = self._next_seq

        # Quality accounting only: retain the established reassembly behavior.
        # A conflict must not become invisible merely because the pending
        # segment later drains and gap_state()/pending counts become empty.
        for start, pending in self._segments.items():
            overlap_start, overlap_end = max(seq, start), min(seq + len(payload), start + len(pending))
            if (overlap_start < overlap_end and
                    payload[overlap_start - seq:overlap_end - seq] != pending[overlap_start - start:overlap_end - start]):
                self.observer_overlap_conflicts += 1
                break

        existing = self._segments.get(seq)
        if existing is None or len(payload) > len(existing):
            self._segments[seq] = payload

        flushed: list[bytes] = []
        while self._next_seq in self._segments:
            chunk = self._segments.pop(self._next_seq)
            flushed.append(chunk)
            self._next_seq += len(chunk)
        return flushed

