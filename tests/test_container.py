"""Test riêng cơ chế container: scope, Lazy, vòng tròn, override."""

from __future__ import annotations

import pytest

from fastapi_modular.core.container import Container, Lazy, Scope, injectable, request_scope


def test_singleton_tra_ve_cung_instance():
    @injectable
    class Alpha:
        pass

    c = Container()
    assert c.resolve(Alpha) is c.resolve(Alpha)


def test_tu_noi_phu_thuoc_theo_annotation():
    @injectable
    class Repo:
        pass

    @injectable
    class Service:
        def __init__(self, repo: Repo) -> None:
            self.repo = repo

    c = Container()
    assert c.resolve(Service).repo is c.resolve(Repo)


def test_lazy_cat_duoc_vong_tron():
    @injectable
    class Left:
        def __init__(self, right: Lazy[Right]) -> None:
            self.right = right

    @injectable
    class Right:
        def __init__(self, left: Left) -> None:
            self.left = left

    c = Container()
    left = c.resolve(Left)
    assert left.right.left is left  # proxy resolve muộn, không đệ quy vô hạn


def test_quen_lazy_thi_bao_loi_ro_rang():
    @injectable
    class Ping:
        def __init__(self, pong: Pong) -> None: ...

    @injectable
    class Pong:
        def __init__(self, ping: Ping) -> None: ...

    with pytest.raises(RuntimeError, match="Vòng tròn phụ thuộc"):
        Container().resolve(Ping)


def test_provider_chua_dang_ky():
    with pytest.raises(RuntimeError, match="Không có provider"):
        Container().resolve("KhongTonTai")


def test_override_cho_test():
    @injectable
    class Real:
        value = "that"

    class Fake:
        value = "gia"

    c = Container()
    c.override(Real, Fake())
    assert c.resolve(Real).value == "gia"


@pytest.mark.asyncio
async def test_request_scope_tao_moi_moi_request():
    @injectable(scope=Scope.REQUEST)
    class PerRequest:
        pass

    c = Container()
    async with request_scope():
        first = c.resolve(PerRequest)
        assert c.resolve(PerRequest) is first
    async with request_scope():
        assert c.resolve(PerRequest) is not first


@pytest.mark.asyncio
async def test_request_scope_goi_on_request_end():
    events: list[str] = []

    @injectable(scope=Scope.REQUEST)
    class Uow:
        async def on_request_end(self, error: BaseException | None) -> None:
            events.append("rollback" if error else "commit")

    c = Container()
    async with request_scope():
        c.resolve(Uow)
    assert events == ["commit"]

    with pytest.raises(ValueError):
        async with request_scope():
            c.resolve(Uow)
            raise ValueError("hỏng")
    assert events == ["commit", "rollback"]


def test_request_scoped_ngoai_request_bao_loi():
    @injectable(scope=Scope.REQUEST)
    class OnlyInRequest:
        pass

    with pytest.raises(RuntimeError, match="request scope"):
        Container().resolve(OnlyInRequest)


def test_singleton_khong_duoc_om_request_scoped():
    @injectable(scope=Scope.REQUEST)
    class Session:
        pass

    @injectable
    class Leaky:
        def __init__(self, session: Session) -> None: ...

    with pytest.raises(RuntimeError, match="request-scoped"):
        Container().resolve(Leaky)


def test_tham_so_thuong_co_mac_dinh_thi_giu_nguyen():
    """Provider được phép có tham số cấu hình thường, không phải phụ thuộc."""

    @injectable
    class CoThamSoThuong:
        def __init__(self, nguong: int = 5, label_: str | None = None) -> None:
            self.nguong = nguong
            self.label_ = label_

    instance = Container().resolve(CoThamSoThuong)
    assert instance.nguong == 5
    assert instance.label_ is None


def test_thieu_provider_va_khong_co_mac_dinh_van_bao_loi():
    @injectable
    class ThieuPhuThuoc:
        # Tên provider không tồn tại — cố ý, để container phải báo lỗi.
        def __init__(self, thu_gi_do: KhongTonTaiDau) -> None: ...  # noqa: F821

    with pytest.raises(RuntimeError, match="Không có provider"):
        Container().resolve(ThieuPhuThuoc)
