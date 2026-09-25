import pytest

from audit_anchor_geometry import is_derived_variant


@pytest.mark.parametrize(
    ("name", "side"),
    [
        ("qsAt.ex-y0.before-may.ex-ext-5", "exit"),
        ("qsFee.ex-y5.before-utter.ex-ext-3", "exit"),
        ("qsTea.ex-con-6", "exit"),
        ("qsTea.en-con-3", "entry"),
        ("qsTea.en-ext-4", "entry"),
    ],
)
def test_every_extension_and_contraction_count_is_derived(name: str, side: str):
    assert is_derived_variant(name, side)
