import pytest

from kvm.edge import CoordinateMapper


def layout():
    return [
        dict(id=0, peer="a", x=-800, y=0, width=800, height=600),
        dict(id=0, peer="b", x=0, y=100, width=1000, height=600),
    ]


def test_same_monitor_ids_and_offsets():
    m = CoordinateMapper("a")
    m.set_layout(layout())
    assert m.check_edge_transition(-1, 200) == ("b", 4, 200)
    assert m.check_edge_transition(-1, 50) is None
    assert m.local_to_global("a", 799, 200) == (-1, 200)
    assert m.global_to_local("b", 4, 200) == (4, 100)


def test_explicit_negative_local_monitor():
    a = layout()
    a[0].update(local_x=-800, local_y=0)
    m = CoordinateMapper("a")
    m.set_layout(a)
    assert m.local_to_global("a", -1, 200) == (-1, 200)


@pytest.mark.parametrize(
    "change", [dict(width=0), dict(width="800"), dict(peer=""), dict(local_x=0)]
)
def test_invalid_monitor(change):
    a = layout()
    a[0].update(change)
    with pytest.raises(ValueError):
        CoordinateMapper("a").set_layout(a)


def test_duplicate_and_overlap():
    a = layout()
    with pytest.raises(ValueError):
        CoordinateMapper("a").set_layout([a[0], a[0]])
    a[1]["x"] = -100
    with pytest.raises(ValueError):
        CoordinateMapper("a").set_layout(a)
