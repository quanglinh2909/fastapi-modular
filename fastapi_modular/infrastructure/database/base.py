"""Hợp đồng chung cho mọi backend database, và phần ánh xạ entity <-> bản ghi.

Ba backend (memory / SQL / MongoDB) đều cài đúng bộ method dưới đây, nên tầng
`Repository` ở core không cần biết đang chạy trên cái nào.

Ánh xạ dựa hoàn toàn vào dataclass entity: tên trường thành tên cột hoặc khoá
document, kiểu Python thành kiểu cột. Entity vì vậy vẫn là dataclass thuần,
không dính ORM.
"""

from __future__ import annotations

import dataclasses
import hashlib
from collections.abc import Callable, Sequence
from datetime import datetime
from enum import Enum
from functools import cache
from typing import Any, Literal, NoReturn, Protocol, TypeVar, get_args, get_type_hints

from fastapi_modular.core.compat import UTC, TimeoutErrors
from fastapi_modular.core.exceptions import BadRequestError

E = TypeVar("E")

Filters = dict[str, Any]
Match = Callable[[Any], bool] | None

# `id` của một bản ghi: chuỗi (khung tự sinh UUID) hoặc số (database tự tăng).
EntityId = str | int


MAX_INDEX_NAME = 63  # PostgreSQL cắt tên định danh ở 63 ký tự


def index_name(prefix: str, storage: str, columns: Sequence[str]) -> str:
    """Tên index ổn định, không vượt giới hạn độ dài của PostgreSQL."""
    name = f"{prefix}_{storage}_{'_'.join(columns)}"
    if len(name) <= MAX_INDEX_NAME:
        return name
    digest = hashlib.sha1("_".join(columns).encode()).hexdigest()[:8]
    return f"{prefix}_{storage[:40]}_{digest}"


class EntityMeta(type):
    """Metaclass làm cho `Event.score >= 0.8` viết được.

    Đọc một trường TỪ LỚP trả về `Column` (đối tượng có nạp chồng `>=`, `==`,
    `<`...), đọc TỪ ĐỐI TƯỢNG vẫn là giá trị thường. Kế thừa `Entity` để bật.

    Vì sao là metaclass chứ không phải descriptor đặt vào thân lớp: descriptor
    phải có `__set__` mới sống chung được với `slots=True`, và khi đó MỌI lần
    đọc/ghi thuộc tính của entity đi qua một lời gọi Python. Đo được: đọc
    thuộc tính 6.8ns -> 66ns, dựng một entity 112ns -> 615ns, và trên đường
    truy vấn thật `find()` 5000 dòng chậm thêm 24% (sqlite) tới 46% (memory).
    Metaclass chỉ chen vào lúc đọc từ LỚP — thứ chỉ xảy ra khi bạn dựng câu
    truy vấn — nên đối tượng entity không mất gì.

    Chỉ chen khi `__dataclass_fields__` đã nằm trong `__dict__` của CHÍNH lớp
    đó, tức `@dataclass` đã chạy xong. Nếu không, một lớp con khai lại trường
    có sẵn (`score: float = 1.0`) sẽ bị `@dataclass` đọc nhầm `Column` thành
    giá trị mặc định.
    """

    def __getattribute__(cls, name: str) -> Any:
        own = type.__getattribute__(cls, "__dict__").get("__dataclass_fields__")
        if own is not None and name in own:
            return _column_of(cls, name)
        return type.__getattribute__(cls, name)


@cache
def _column_of(entity: type, name: str) -> Any:
    """`Column` của một trường, dựng một lần rồi dùng lại."""
    from fastapi_modular.infrastructure.database.query import Column

    return Column(entity, name)


class Entity(metaclass=EntityMeta):
    """Kế thừa để viết điều kiện bằng toán tử thường thay vì đuôi `__gte`.

        @entity()
        @dataclass(slots=True)
        class Event(Entity):
            id: str
            score: float

        await repo.query().where(Event.score >= 0.8, Event.label == "person").all()

    Không kế thừa cũng không sao: `.where(score__gte=0.8)` và `F(Event).score`
    vẫn dùng được y như trước.

    `__slots__ = ()` để lớp con khai `slots=True` không bị mọc lại `__dict__` —
    thiếu dòng này thì kế thừa `Entity` âm thầm làm entity to ra.
    """

    __slots__ = ()


OnDelete = Literal["CASCADE", "SET NULL", "SET DEFAULT", "RESTRICT", "NO ACTION"]

_METADATA_KEY = "fastapi_modular.reference"


@dataclasses.dataclass(frozen=True, slots=True)
class Reference:
    """Một cột trỏ sang bảng khác — khoá ngoại.

    `on_delete` quyết định chuyện gì xảy ra với **bản ghi con** khi bản ghi cha
    bị xoá. Đây là câu hỏi nghiệp vụ, không phải chi tiết kỹ thuật, nên khung
    bắt bạn nói rõ thay vì đoán:

        "CASCADE"      xoá camera -> xoá luôn mọi sự kiện của nó
        "SET NULL"     xoá camera -> sự kiện còn đó, camera_id thành NULL
        "SET DEFAULT"  xoá camera -> camera_id về giá trị mặc định của trường
        "RESTRICT"     còn sự kiện thì KHÔNG cho xoá camera (lỗi 409)
        "NO ACTION"    để database tự quyết (thường giống RESTRICT)
    """

    target: type
    on_delete: OnDelete = "RESTRICT"
    column: str = "id"          # cột bên bảng cha; gần như luôn là khoá chính


def reference(target: type, *, on_delete: OnDelete = "RESTRICT", column: str = "id") -> dict:
    """Khai một cột là khoá ngoại. Đặt vào `metadata=` của trường:

        @entity()
        @dataclass(slots=True)
        class Event:
            id: str
            camera_id: str = field(metadata=reference(Camera, on_delete="CASCADE"))

    Đặt ngay trên cột chứ không phải trên `@entity(...)`: khoá ngoại nói về
    MỘT cột cụ thể, và để cạnh nhau thì đọc một dòng là biết. TypeORM và Django
    đều đặt ở đây.
    """
    if on_delete not in get_args(OnDelete):
        raise BadRequestError(
            f"`on_delete` phải là một trong {', '.join(get_args(OnDelete))} "
            f"(đang là {on_delete!r})"
        )
    return {_METADATA_KEY: Reference(target=target, on_delete=on_delete, column=column)}


def references_of(entity: type) -> dict[str, Reference]:
    """{tên cột: Reference} của một entity, đọc từ metadata của dataclass."""
    found: dict[str, Reference] = {}
    for field in dataclasses.fields(entity):
        ref = field.metadata.get(_METADATA_KEY)
        if ref is not None:
            found[field.name] = ref
    return found


_COLUMN_KEY = "fastapi_modular.column"


@dataclasses.dataclass(frozen=True, slots=True)
class ColumnSpec:
    """Cột chữ được tạo dưới database bằng kiểu nào.

    Mặc định (không khai gì) là `VARCHAR` không giới hạn — Postgres nhận, SQLite
    nhận. Khai độ dài khi bạn muốn database CHẶN dữ liệu quá dài, khai `text`
    khi trường có thể rất dài và giới hạn nào cũng là võ đoán.
    """

    length: int | None = None
    text: bool = False


def column(*, length: int | None = None, text: bool = False) -> dict:
    """Chọn kiểu cột cho một trường chữ. Đặt vào `metadata=` của trường:

        @entity()
        @dataclass(slots=True)
        class Camera(Entity):
            id: str
            name: str = field(default="", metadata=column(length=50))    # VARCHAR(50)
            note: str = field(default="", metadata=column(text=True))    # TEXT

    Độ dài được kiểm CẢ ở tầng khung, trước khi câu lệnh xuống database: SQLite
    bỏ qua `VARCHAR(50)` (ghi 60 ký tự vẫn lọt) còn Postgres thì báo lỗi, nên
    nếu chỉ dựa vào database thì cùng một đoạn code chạy được lúc dev và đổ lúc
    chạy thật. MongoDB cũng không có khái niệm độ dài — kiểm ở đây là chỗ duy
    nhất bốn backend giống nhau.

    Chung trường với khoá ngoại thì gộp hai dict bằng `|`:

        camera_id: str = field(metadata=reference(Camera) | column(length=36))
    """
    if length is not None and text:
        raise BadRequestError(
            "`column(length=..., text=True)` mâu thuẫn: TEXT là kiểu không giới hạn. "
            "Chọn một trong hai."
        )
    if length is None and not text:
        raise BadRequestError(
            "`column()` phải khai `length=` hoặc `text=True`; không khai gì thì "
            "bỏ hẳn `metadata=column()` đi, cột vẫn là VARCHAR như cũ."
        )
    if length is not None and (isinstance(length, bool) or not isinstance(length, int) or length < 1):
        raise BadRequestError(f"`length` phải là số nguyên dương (đang là {length!r})")
    return {_COLUMN_KEY: ColumnSpec(length=length, text=text)}


def columns_of(entity: type) -> dict[str, ColumnSpec]:
    """{tên cột: ColumnSpec} của một entity, đọc từ metadata của dataclass."""
    found: dict[str, ColumnSpec] = {}
    for field in dataclasses.fields(entity):
        spec = field.metadata.get(_COLUMN_KEY)
        if spec is not None:
            found[field.name] = spec
    return found


def _is_text_type(declared: Any) -> bool:
    """Kiểu này có lưu bằng cột chữ không — `str`, `Enum`, hoặc Optional của chúng."""
    if declared is str:
        return True
    if isinstance(declared, type) and issubclass(declared, Enum):
        return True
    args = [arg for arg in get_args(declared) if arg is not type(None)]
    return bool(args) and all(_is_text_type(arg) for arg in args)


def check_lengths(entity: type, values: Any) -> None:
    """Chặn chuỗi dài hơn `length` đã khai, TRƯỚC khi xuống database.

    `values` là dict (đường `update`) hoặc chính entity (đường `save`).

    Vì sao khung tự kiểm thay vì để database kiểm: chỉ Postgres báo lỗi. Đo
    được — ghi 60 ký tự vào `VARCHAR(50)`: SQLite nhận, Postgres ném
    `StringDataRightTruncation`. Không kiểm ở đây thì `fam test` trên SQLite
    xanh còn production đổ, đúng loại lỗi khó tìm nhất.
    """
    limits = _max_lengths(entity)
    if not limits:
        return
    read = values.get if isinstance(values, dict) else lambda name: getattr(values, name, None)
    for name, limit in limits.items():
        value = bind_value(read(name))
        if isinstance(value, str) and len(value) > limit:
            raise BadRequestError(
                f"{entity.__name__}.{name} dài {len(value)} ký tự, quá {limit} ký tự "
                f"đã khai bằng `column(length={limit})`. Cắt bớt trước khi ghi, hoặc "
                f"nâng độ dài trong entity rồi chạy migration đổi cột."
            )


@cache
def _max_lengths(entity: type) -> dict[str, int]:
    """{tên cột: độ dài tối đa} — chỉ những cột có khai `length`."""
    return {
        name: spec.length
        for name, spec in mapping_for(entity).column_specs
        if spec.length is not None
    }


def default_of(entity: type, column: str) -> Any:
    """Giá trị mặc định của một trường — dùng cho `SET DEFAULT`."""
    for field in dataclasses.fields(entity):
        if field.name != column:
            continue
        if field.default is not dataclasses.MISSING:
            return field.default
        if field.default_factory is not dataclasses.MISSING:   # type: ignore[misc]
            return field.default_factory()                     # type: ignore[misc]
        raise BadRequestError(
            f"{entity.__name__}.{column} khai `on_delete=\"SET DEFAULT\"` nhưng "
            "trường này không có giá trị mặc định. Thêm `= giá_trị` vào khai báo."
        )
    raise BadRequestError(f"{entity.__name__} không có trường {column!r}")


@dataclasses.dataclass(frozen=True, slots=True)
class EntityMapping:
    entity: type
    storage: str                 # tên bảng (SQL) hoặc collection (Mongo)
    fields: dict[str, type]      # tên trường -> kiểu đã giải
    unique: tuple[tuple[str, ...], ...]   # mỗi phần tử là một cột hoặc một cụm cột
    indexes: tuple[tuple[str, ...], ...]
    references: tuple[tuple[str, Reference], ...] = ()   # (tên cột, khoá ngoại)
    column_specs: tuple[tuple[str, ColumnSpec], ...] = ()  # (tên cột, kiểu cột chữ)
    auto_id: bool = False        # `id: int` -> database tự tăng, khung không sinh

    def index_specs(self) -> list[tuple[str, tuple[str, ...], bool]]:
        """[(tên index, các cột, có unique không)] cho mọi index đã khai báo."""
        return [
            *((index_name("uq", self.storage, cols), cols, True) for cols in self.unique),
            *((index_name("ix", self.storage, cols), cols, False) for cols in self.indexes),
        ]


@cache
def mapping_for(entity: type) -> EntityMapping:
    if not dataclasses.is_dataclass(entity):
        raise TypeError(f"{entity.__name__} phải là dataclass mới ánh xạ được")

    hints = get_type_hints(entity)
    fields = {f.name: hints.get(f.name, str) for f in dataclasses.fields(entity)}

    specs = columns_of(entity)
    for name, spec in specs.items():
        if _is_text_type(fields[name]):
            continue
        declared_as = "text=True" if spec.text else f"length={spec.length}"
        type_name = getattr(fields[name], "__name__", fields[name])
        raise BadRequestError(
            f"{entity.__name__}.{name} khai `column({declared_as})` nhưng kiểu là "
            f"{type_name}. Độ dài và TEXT chỉ đặt được cho cột chữ (`str` hoặc `Enum`)."
        )
    return EntityMapping(
        entity=entity,
        storage=getattr(entity, "__storage_name__", f"{entity.__name__.lower()}s"),
        fields=fields,
        unique=tuple(getattr(entity, "__storage_unique__", ())),
        indexes=tuple(getattr(entity, "__storage_indexes__", ())),
        references=tuple(references_of(entity).items()),
        column_specs=tuple(specs.items()),
        auto_id=fields.get("id") is int,
    )


def to_document(obj: Any) -> dict[str, Any]:
    """Entity -> dict thuần (Enum thành giá trị, datetime giữ nguyên)."""
    doc: dict[str, Any] = {}
    for field in dataclasses.fields(obj):
        value = getattr(obj, field.name)
        doc[field.name] = value.value if isinstance(value, Enum) else value
    return doc


def _allows_none(declared: Any) -> bool:
    """Kiểu có chấp nhận None không (Optional[X] hoặc X | None)."""
    return declared is Any or type(None) in get_args(declared)


def unwrap_optional(declared: Any) -> Any:
    """`int | None` -> `int`. Kiểu không phải Optional thì trả lại nguyên xi.

    Phải có vì `X | None` KHÔNG phải một `type`: mọi phép `is datetime` hay
    `issubclass(..., Enum)` đều trượt, và cột đi theo nhánh mặc định. Đo được
    trước khi có hàm này: `port: int | None` sinh ra cột `VARCHAR`, nên SQLite
    đọc về `'8080'` (chuỗi) trong khi memory đọc về `8080`, còn Postgres thì ném
    `DataError` ngay lúc ghi. Cùng một entity, ba kết quả.
    """
    args = [arg for arg in get_args(declared) if arg is not type(None)]
    return args[0] if len(args) == 1 else declared


@cache
def _defaults_of(entity: type) -> dict[str, Callable[[], Any]]:
    """Hàm sinh giá trị mặc định cho từng trường có khai báo default."""
    out: dict[str, Callable[[], Any]] = {}
    for f in dataclasses.fields(entity):
        if f.default is not dataclasses.MISSING:
            out[f.name] = lambda value=f.default: value
        elif f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
            out[f.name] = f.default_factory  # type: ignore[assignment]
    return out


def coerce_value(declared: Any, value: Any) -> Any:
    """Giá trị thô từ driver -> đúng kiểu đã khai trong entity.

    Dùng ở hai chỗ: dựng entity, và dựng dict của `.select()`/`.fields()`. Phải
    là một hàm dùng chung, vì nếu chỉ entity được ép kiểu thì `.select()` trên
    sqlite trả chuỗi `'online'` còn trên memory trả `Trang_thai.ON` — test xanh
    ở memory rồi hỏng ở production, đúng kiểu lỗi khó tìm nhất.
    """
    if value is None:
        return None
    declared = unwrap_optional(declared)
    if isinstance(declared, type) and issubclass(declared, Enum):
        return declared(value)
    if declared is datetime:
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        if isinstance(value, datetime) and value.tzinfo is None:
            # SQLite và MongoDB trả datetime KHÔNG mang múi giờ (cả hai lưu
            # theo UTC nhưng không kèm tzinfo). Gắn lại UTC ở đây để ba driver
            # cho ra cùng một dạng, và để một response không lẫn lộn "có Z"
            # với "không Z".
            value = value.replace(tzinfo=UTC)
    return value


def from_document(entity: type[E], doc: dict[str, Any]) -> E:
    """dict -> entity, ép lại Enum/datetime và bù giá trị mặc định.

    Bù mặc định là chỗ quan trọng khi schema tiến hoá: thêm một trường vào
    entity thì bản ghi cũ chưa có giá trị (SQL trả NULL, Mongo thiếu hẳn khoá).
    Nếu kiểu khai báo không nhận None mà entity có default, ta dùng default —
    nhờ vậy dữ liệu cũ đọc ra vẫn hợp lệ thay vì mang None trái kiểu.
    """
    mapping = mapping_for(entity)
    defaults = _defaults_of(entity)
    kwargs: dict[str, Any] = {}

    for name, declared in mapping.fields.items():
        value = doc.get(name)

        if value is None and not _allows_none(declared) and name in defaults:
            value = defaults[name]()
        else:
            value = coerce_value(declared, value)

        kwargs[name] = value

    return entity(**kwargs)  # type: ignore[call-arg]


def check_changes(entity: type, changes: Filters) -> dict[str, Any]:
    """Soi bộ giá trị sắp ghi đè: cột phải có thật, và không được đụng `id`.

    Cùng lý do với `active_filters`: gõ sai tên cột mà im lặng bỏ qua thì câu
    lệnh chạy xong, trả về "đã sửa N dòng", và không sửa gì cả.

    `id` bị chặn riêng: nó là danh tính của bản ghi và là thứ mọi khoá ngoại
    đang trỏ tới. Đổi nó bằng một lệnh UPDATE hàng loạt là cách nhanh nhất để
    có một đống bản ghi con mồ côi.
    """
    if not changes:
        raise BadRequestError(
            f"`update` trên {entity.__name__} không có giá trị nào để ghi. "
            f"Truyền `{{'ten_cot': gia_tri}}` hoặc `ten_cot=gia_tri`."
        )
    known = mapping_for(entity).fields
    for name, value in changes.items():
        if name == "id":
            raise BadRequestError(
                f"Không đổi được `id` của {entity.__name__} bằng `update`: nó là "
                f"danh tính của bản ghi, và khoá ngoại của bảng khác đang trỏ vào. "
                f"Cần đổi thật thì tạo bản ghi mới rồi chuyển các bản ghi con sang."
            )
        if name not in known:
            raise BadRequestError(
                f"{entity.__name__} không có trường {name!r}. "
                f"Có: {', '.join(sorted(known))}"
            )
        check_value(value, f"Giá trị của {name!r}")
    check_lengths(entity, changes)
    return {name: bind_value(value) for name, value in changes.items()}


def bind_value(value: Any) -> Any:
    """Giá trị đem đi so sánh — Enum quy về `.value`, danh sách quy từng phần tử.

    Vì sao phải có: cột Enum được LƯU bằng `.value` (SQL lưu chuỗi, Mongo cũng
    vậy), nên mọi phép so phải so trên `.value`. Thiếu chỗ này thì ba backend
    lệch nhau theo đúng kiểu tệ nhất — đo được với Enum THƯỜNG (không phải
    StrEnum): `find(kind=Kind.B)` chạy trên memory nhưng NỔ trên sqlite
    ("type 'Kind' is not supported") lẫn mongo ("cannot encode object"), tức
    `fam test` xanh mà production đổ. Chiều ngược lại cũng lệch: lọc bằng chuỗi
    `"dac_biet"` thì SQL khớp (so chuỗi với chuỗi) còn memory trượt (so chuỗi
    với Enum).
    """
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (list, tuple, set)):
        return [bind_value(v) for v in value]
    return value


def matches(obj: Any, filters: Filters, match: Match) -> bool:
    """Lọc trong Python — dùng cho backend memory và cho tham số `match=`."""
    for key, value in filters.items():
        if bind_value(getattr(obj, key, None)) != bind_value(value):
            return False
    return match is None or match(obj)


def check_value(value: Any, where: str = "điều kiện") -> Any:
    """Chặn giá trị mang toán tử của MongoDB. Trả lại chính nó nếu sạch.

    Ở MongoDB, một giá trị dạng `{"$ne": ""}` KHÔNG được so bằng — nó thành
    toán tử, và điều kiện coi như bị bỏ. Đo được: `find(name="an",
    token={"$ne": ""})` trả về bản ghi của người khác, tức là qua được cửa đăng
    nhập. Kẻ tấn công chỉ cần gửi JSON `{"token": {"$ne": ""}}`.

    Chặn ở tầng dùng chung để ba backend cùng từ chối một kiểu, thay vì
    postgres ném lỗi driver khó hiểu còn mongo thì lặng lẽ cho qua.
    """
    if isinstance(value, dict):
        operators = [k for k in value if isinstance(k, str) and k.startswith("$")]
        if operators:
            raise BadRequestError(
                f"{where}: giá trị chứa toán tử của database ({', '.join(operators)}). "
                "Gần như luôn là dữ liệu người dùng gửi lên thẳng vào truy vấn — "
                "ép kiểu nó về str/int/bool trước (pydantic làm sẵn việc này)."
            )
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            check_value(item, where)
    return value


def active_filters(filters: Filters, entity: type | None = None) -> Filters:
    """Bỏ điều kiện có giá trị None (quy ước: None = không lọc), và soi tên cột.

    Tên cột lạ bị TỪ CHỐI chứ không bỏ qua. Bỏ qua nghe có vẻ hiền, nhưng đo
    được: `find(**{"$where": "1 == 1"})` trên SQL trả về TOÀN BỘ bảng (bộ lọc
    biến mất) còn trên Mongo thì chạy JavaScript ngay trên server. Cùng một
    hàm mà một bên lộ dữ liệu, một bên chạy mã lạ.
    """
    cleaned = {k: v for k, v in filters.items() if v is not None}
    if entity is None:
        return cleaned

    known = mapping_for(entity).fields
    for name, value in cleaned.items():
        if name not in known:
            raise BadRequestError(
                f"{entity.__name__} không có trường {name!r}. "
                f"Có: {', '.join(sorted(known))}"
            )
        check_value(value, f"Điều kiện {name!r}")
    return cleaned


class RollbackRequested(Exception):
    """Tín hiệu nội bộ của `tx.rollback()`. Không lọt ra ngoài khối transaction."""


class Transaction:
    """Cái `async with db.transaction() as tx:` trả về.

    Khối tự commit khi thoát êm và tự rollback khi có exception, nên phần lớn
    trường hợp không cần đụng tới `tx`. Nó có đúng một việc: huỷ giữa chừng mà
    KHÔNG phải ném lỗi ra ngoài — thứ mà `async with` trần không làm được.
    """

    __slots__ = ("rolled_back",)

    def __init__(self) -> None:
        self.rolled_back = False

    async def rollback(self) -> NoReturn:
        """Huỷ mọi thay đổi trong khối và thoát khối ngay tại đây.

            async with db.transaction() as tx:
                await repo.save(...)
                if không_hợp_lệ:
                    await tx.rollback()      # thoát khối, KHÔNG ném lỗi ra ngoài
                await repo2.save(...)        # dòng này không chạy
        """
        self.rolled_back = True
        raise RollbackRequested


class DuplicateKeyViolation(Exception):
    """Vi phạm ràng buộc duy nhất, do backend không có sẵn kiểu lỗi riêng.

    SQL ném IntegrityError, Mongo ném DuplicateKeyError; backend memory dùng
    lớp này để ba đường đi cho ra cùng một kết quả HTTP 409.
    """

    def __init__(self, storage: str, columns: Sequence[str], values: Sequence[Any]) -> None:
        self.storage = storage
        self.columns = tuple(columns)
        self.values = tuple(values)
        pairs = ", ".join(f"{c}={v!r}" for c, v in zip(self.columns, self.values, strict=True))
        super().__init__(f"{storage}: đã tồn tại bản ghi với {pairs}")


# Tên lớp lỗi "tạm thời" của các driver. Dùng tên thay vì import để file này
# không phụ thuộc vào thư viện của driver nào.
_TRANSIENT_NAMES = frozenset({
    "ServerSelectionTimeoutError",   # pymongo: chưa chọn được server
    "AutoReconnect",                 # pymongo: mất kết nối, sẽ tự nối lại
    "NetworkTimeout",                # pymongo
    "ConnectionFailure",             # pymongo (lớp cha)
    "CannotConnectNowError",         # asyncpg: database đang khởi động
    "TooManyConnectionsError",       # asyncpg: hết slot, chờ chút sẽ có
})


def is_transient_error(exc: BaseException) -> bool:
    """Lỗi này có khả năng tự hết nếu thử lại không?

    Phân biệt "database chưa kịp lên" (đáng thử lại) với "sai mật khẩu / sai
    tên database" (thử lại bao nhiêu lần cũng vậy, chỉ làm chậm lúc phát hiện
    cấu hình sai). Duyệt cả chuỗi __cause__ vì driver hay bọc lỗi gốc lại.
    """
    seen: set[int] = set()
    current: BaseException | None = exc

    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (ConnectionError, *TimeoutErrors)):
            return True
        if type(current).__name__ in _TRANSIENT_NAMES:
            return True
        # OSError chung (host chưa phân giải được, mạng chưa lên...)
        if isinstance(current, OSError) and not isinstance(current, (IsADirectoryError, NotADirectoryError, PermissionError)):
            return True
        current = current.__cause__ or current.__context__

    return False


class DatabaseBackend(Protocol):
    """Bộ method mà mọi backend phải có."""

    name: str

    async def startup(self) -> None: ...
    async def shutdown(self) -> None: ...
    async def ping(self) -> bool: ...

    async def get(self, entity: type[E], id_: EntityId) -> E | None: ...
    async def find(
        self,
        entity: type[E],
        *,
        filters: Filters,
        match: Match = None,
        order_by: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[E]: ...
    async def find_one(
        self, entity: type[E], *, filters: Filters, match: Match = None
    ) -> E | None: ...
    async def count(
        self, entity: type[E], *, filters: Filters, match: Match = None
    ) -> int: ...
    # Query builder. `memory` và SQL cài đủ; `mongodb` ném lỗi nói rõ vì sao.
    async def run_query(self, spec: Any) -> list[Any]: ...
    async def count_query(self, spec: Any) -> int: ...

    async def save(self, entity: type[E], obj: E) -> E: ...
    async def delete(self, entity: type[E], id_: EntityId) -> bool: ...
    async def delete_where(
        self, entity: type[E], *, filters: Filters, match: Match = None
    ) -> int: ...
    async def update_one(
        self, entity: type[E], *, id_: EntityId, changes: Filters
    ) -> E | None: ...
    async def update_where(
        self, entity: type[E], *, filters: Filters, changes: Filters, match: Match = None
    ) -> int: ...
