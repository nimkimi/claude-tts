from sonari.platform import get_platform
from sonari.platform.base import RaiseBackend, NoopRaiseBackend, PlatformBackend


def test_platformbackend_has_raise_backend_field():
    assert "raise_backend" in PlatformBackend.__dataclass_fields__


def test_get_platform_exposes_a_raise_backend():
    rb = get_platform().raise_backend
    assert isinstance(rb, RaiseBackend)


def test_noop_backend_is_inert():
    nb = NoopRaiseBackend()
    assert nb.supports(None) is False
    assert nb.raise_session(None) is False
    assert nb.check_grant() == "unsupported"
    assert nb.doctor_rows() == []
