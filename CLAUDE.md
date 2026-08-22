# Hướng dẫn cho Claude Code

## Kiến trúc

Hai khối, ranh giới là điều quan trọng nhất:

- **`pymodular/`** — THƯ VIỆN, thứ được đóng gói và cài bằng pip. **Không import
  bất cứ thứ gì từ `src/`.** Nó chỉ biết "có một package tên `src.api`, quét nó đi"
  (`DEFAULT_PACKAGE = "src.api"` trong `discovery.py`).
- **`src/`** — ỨNG DỤNG MẪU của repo này, không nằm trong gói cài. Xoá được.

Trong `pymodular/`: `core/` (DI, controller, config, guard, WebSocket) ·
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
| lệnh hoặc cờ trong `cli/` | bảng lệnh ở `README.md`, cây `cli/` ở `README.md` **và** `docs/architecture.md` |
| tên file / lớp mà `cli/new_module.py` sinh ra | mục "Thêm module mới" ở `docs/architecture.md` |
| API công khai (`pymodular/__init__.py`, decorator, method) | doc của phần đó, và `docs/README.md` nếu đổi bảng đối chiếu |
| thêm/bớt test | con số test ở `README.md` (cây thư mục) và `docs/architecture.md` (mục Chất lượng mã) |

Hai cái bẫy đã từng làm docs sai:

1. **Con số viết tay** — "299 test", "78 test", "11 biến". Đo lại trước khi giữ
   nguyên, đừng chép.
2. **Bảng liệt kê** — thêm một biến vào `Settings` hay một lệnh vào CLI mà quên
   thêm dòng vào bảng thì bảng lặng lẽ thiếu, không ai báo.

## Kiểm chứng trước khi nói là xong

```bash
pytest -q                              # 341 passed, 40 skipped (40 skip cần hạ tầng thật)
ruff check pymodular src tests         # KHÔNG dùng `pym lint` trần: mặc định của nó
                                       # trỏ vào thư mục `app` không tồn tại
```

`tests/test_configure_env.py::test_bien_nhac_trong_docs_deu_con_that` đối chiếu
mọi biến `APP_*` trong docs với `Settings` thật. Thêm một **nhóm** biến mới
(`APP_<TÊN>__*`) thì phải thêm nhóm đó vào bảng `nhom` trong chính test, nếu
không biến có thật vẫn bị báo là "không thuộc nhóm nào".

Số test ở đây cũng là con số viết tay — sửa khi nó đổi.

## Ngôn ngữ

Tài liệu, comment, docstring và commit message viết **tiếng Việt**. API công khai
(tên hàm, tên lớp, tên biến `APP_*`) viết **tiếng Anh**. Giữ đúng quy ước này.
