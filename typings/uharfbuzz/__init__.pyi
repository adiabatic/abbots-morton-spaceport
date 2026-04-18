from collections.abc import Sequence

class GlyphInfo:
    codepoint: int
    cluster: int

class GlyphPosition:
    x_advance: int
    y_advance: int
    x_offset: int
    y_offset: int

class Blob:
    @staticmethod
    def from_file_path(path: str) -> Blob: ...

class Face:
    def __init__(self, blob: Blob, index: int = 0) -> None: ...

class Font:
    def __init__(self, face: Face) -> None: ...
    def glyph_to_string(self, glyph: int) -> str: ...

class Buffer:
    def __init__(self) -> None: ...
    def add_str(self, text: str) -> None: ...
    def guess_segment_properties(self) -> None: ...
    @property
    def glyph_infos(self) -> list[GlyphInfo]: ...
    @property
    def glyph_positions(self) -> list[GlyphPosition]: ...

def shape(
    font: Font,
    buffer: Buffer,
    features: dict[str, bool] | None = None,
) -> None: ...
