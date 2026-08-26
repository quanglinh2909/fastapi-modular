# Hướng dẫn cho Claude Code

## Kiến trúc

Hai khối, ranh giới là điều quan trọng nhất:

- **`fastapi_modular/`** — THƯ VIỆN, thứ được đóng gói và cài bằng pip. **Không import
  bất cứ thứ gì từ `src/`.** Nó chỉ biết "có một package tên `src.api`, quét nó đi"
  (`DEFAULT_PACKAGE = "src.api"` trong `discovery.py`).
- **`src/`** — ỨNG DỤNG MẪU của repo này, không nằm trong gói cài. Xoá được.

Trong `fastapi_modular/`: `core/` (DI, controller, config, guard, WebSocket) ·
`infrastructure/` (database, rabbitmq, redis, mqtt, kafka — **mỗi hạ tầng một
package, không biết nhau**) · `cli/` · `factory.py` · `discovery.py`.

Mọi thứ ngoài database đều **mặc định tắt**, thư viện chỉ được import khi bật.

Chi tiết đầy đủ: [docs/architecture.md](docs/architecture.md). Đọc file đó trước
khi sửa thứ gì đụng tới cấu trúc.

## Quy tắc BẮT BUỘC: sửa code là phải sửa docs

Mỗi thay đổi code phải cập nhật tài liệu tương ứng **trong cùng commit**. Docs ở
đây là tài liệu tra cứu, người đọc tin vào từng con số và từng tên cờ — lệch một
chỗ là họ gõ theo rồi lỗi.

| Sửa ở đâu | Bắt buộc soi lại |
|---|---|
| trường trong `core/config.py` (`Settings` và các lớp con) | `docs/config.md` (bảng biến), và doc của nhóm đó: `database.md` / `websocket.md` / `rabbitmq.md` / `redis.md` / `mqtt.md` / `kafka.md` |
| lệnh hoặc cờ trong `cli/` | bảng lệnh ở **`README.md` VÀ `README.vi.md`**, cây `cli/` ở cả hai README **và** `docs/architecture.md` |
| tên file / lớp mà `cli/new_module.py` sinh ra | mục "Thêm module mới" ở `docs/architecture.md` |
| `core/providers.py` hoặc `cli/new_provider.py` | `docs/providers.md` |
| `core/rpc.py`, `*/responders.py`, `emit`/`send` | `docs/rpc.md`, và bảng đối chiếu NestJS ở **cả hai README** + `docs/architecture.md` |
| `core/scheduler.py`, `core/cron.py`, `core/jobs.py`, `core/workers.py`, `core/events.py`, `core/locks.py` | `docs/background.md`, bảng nhóm biến ở `docs/config.md`, bảng `groups` trong `tests/test_configure_env.py`, và bảng đối chiếu NestJS ở **cả hai README** |
| API công khai (`fastapi_modular/__init__.py`, decorator, method) | doc của phần đó, và `docs/README.md` nếu đổi bảng đối chiếu |
| thêm/bớt test | con số test ở **cả hai README** (cây thư mục) và `docs/architecture.md` (mục Chất lượng mã) |

Hai cái bẫy đã từng làm docs sai:

1. **Con số viết tay** — "299 test", "78 test", "11 biến". Đo lại trước khi giữ
   nguyên, đừng chép.
2. **Bảng liệt kê** — thêm một biến vào `Settings` hay một lệnh vào CLI mà quên
   thêm dòng vào bảng thì bảng lặng lẽ thiếu, không ai báo.

## README song ngữ: sửa một bản là phải sửa bản kia

Có **hai** README, nội dung phải luôn tương đương:

| File | Ngôn ngữ | Ai đọc |
|---|---|---|
| `README.md` | tiếng Anh | trang PyPI, Google, người dùng quốc tế |
| `README.vi.md` | tiếng Việt | người dùng Việt Nam |

**Sửa một bản mà quên bản kia là lỗi.** Hai bản lệch nhau còn tệ hơn chỉ có một
bản: người đọc tin vào bản họ đang mở, và không ai biết bản nào mới hơn.

Quy tắc: mỗi thay đổi nội dung ở một README phải có thay đổi tương ứng ở README
kia **trong cùng commit**. Đổi bảng lệnh, đổi con số, thêm mục, sửa ví dụ — tất
cả đều tính. Chỉ sửa lỗi chính tả riêng của một ngôn ngữ thì không cần.

Hai bản giữ **cùng thứ tự mục** để đối chiếu nhanh: mở cạnh nhau là thấy ngay
bên nào thiếu. Đừng đổi thứ tự mục ở một bản mà không đổi bản kia.

`pyproject.toml` khai `readme = "README.md"`, nên **bản tiếng Anh là bản hiện
trên PyPI**. Link trong `README.md` phải là URL tuyệt đối (xem mục Phát hành);
link trong `docs/*.md` thì cứ để tương đối.

## Kiểm chứng trước khi nói là xong

```bash
pytest -q                       # 901 passed, 71 skipped (71 skip cần hạ tầng hoặc driver thật)
fam lint fastapi_modular src tests    # `fam lint` trần chỉ soi `src`, thiếu thư viện và test
```

`tests/test_configure_env.py::test_bien_nhac_trong_docs_deu_con_that` đối chiếu
mọi biến `APP_*` trong docs với `Settings` thật. Thêm một **nhóm** biến mới
(`APP_<TÊN>__*`) thì phải thêm nhóm đó vào bảng `nhom` trong chính test, nếu
không biến có thật vẫn bị báo là "không thuộc nhóm nào".

Số test ở đây cũng là con số viết tay — sửa khi nó đổi.

## Phát hành

Mỗi lần đẩy lên PyPI **bắt buộc** tạo một nhánh git mang đúng số phiên bản đó
(`v0.2.0`), đẩy nhánh lên remote, rồi mới quay lại `main`. Nhánh là ảnh chụp
đúng thứ đã lên PyPI — bản trên PyPI không sửa lại được, nên phải có một chỗ
trong git tương ứng một-đối-một với nó.

```bash
# sau khi đã commit mọi thay đổi trên main
python -m build && python -m twine check dist/*
python -m twine upload dist/*
git branch v0.2.0 && git push -u origin v0.2.0
```

Số phiên bản chỉ nằm ở MỘT chỗ: `__version__` trong
`fastapi_modular/__init__.py` (pyproject khai `dynamic = ["version"]`).

Hai điều đã cắn một lần:

- **PyPI chặn tên "quá giống" project đã có**, không chỉ tên trùng khít. Nó so
  sau khi bỏ hết `-`, `_`, `.` — vì vậy `pymodular` bị từ chối do đụng
  `py-modular`. HTTP 404 ở `/pypi/<tên>/json` KHÔNG đủ để kết luận tên dùng
  được; phải kiểm cả các biến thể dấu ngăn.
- **README là trang hiển thị trên PyPI**, nên mọi link trong đó phải là URL
  tuyệt đối. Link tương đối kiểu `docs/x.md` phân giải thành
  `pypi.org/project/.../docs/x.md` và trả 404. Link giữa các file trong `docs/`
  thì cứ để tương đối.

## Ngôn ngữ

**Chữ nghĩa** — tài liệu, comment, docstring, commit message, thông báo lỗi cho
người dùng: **tiếng Việt**.

**Mọi định danh trong code — tiếng Anh, không trừ cái nào.** Tên hàm, tên lớp,
tên biến (kể cả biến cục bộ và hàm `_private`), tên tham số, tên file `.py`,
khoá dict dùng như trường dữ liệu, `dest=` của argparse. Không có ngoại lệ cho
"chỉ dùng nội bộ".

```python
# ĐÚNG
async def _dispatch(self, message: Any) -> None:
    """Giao tin cho handler khớp pattern."""
    reply_topic = ...

# SAI — tên tiếng Việt, dù comment đúng
async def _giao(self, message: Any) -> None:
    topic_tra_loi = ...
```

Nếu phải đổi tên hàng loạt, **đừng dùng `sed`**: nó sửa cả chữ trong comment và
trong chuỗi. Đổi bằng `tokenize` (chỉ đụng token `NAME`) rồi soi tay bốn chỗ mà
tokenizer KHÔNG với tới, vì ở đó tên nằm trong chuỗi:

| Chỗ | Ví dụ |
|---|---|
| `__slots__`, `__all__` | `__slots__ = ("_pending", "_name")` |
| `@pytest.mark.parametrize` | `parametrize("raw,expected", ...)` |
| argparse | `add_argument("ten")` -> `args.ten`; `dest="lenh"` |
| placeholder trong template | `"{ten}".format(...)`, `@get("/x/{ma}")` |

Và soi lại **nghĩa** của tên mới, đừng tin bảng tra. Lần đổi vừa rồi từng biến
`rong` (rộng) thành `empty`, `tang` (tầng) thành `increase`, và `ma` (mã đơn)
thành `correlation_id` trong một model dữ liệu — code vẫn chạy nhưng tên sai
nghĩa, tệ hơn tên tiếng Việt cũ.
