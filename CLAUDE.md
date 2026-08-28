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

## Docs trong `docs/` là HƯỚNG DẪN DÙNG, không phải tài liệu kỹ thuật

Người đọc là **người chưa biết dùng tính năng đó**. Họ mở trang này với một câu
hỏi dạng "tôi muốn làm X thì viết thế nào", không phải "cơ chế bên trong ra
sao". Viết cho họ.

Mỗi trang đi theo đúng thứ tự này:

| Thứ tự | Phần | Nội dung |
|---|---|---|
| 1 | **Bạn đang cần làm gì?** | bảng "việc muốn làm" -> link tới mục. Câu chữ lấy từ miệng người dùng ("cứ 5 giây kiểm tra camera"), không phải tên kỹ thuật ("periodic scheduling") |
| 2 | **Làm thế nào** | ví dụ chép-dán-chạy được: đủ import, đủ `@injectable`, ghi rõ file đặt ở đâu |
| 3 | **Kiểm xem nó chạy chưa** | dòng log phải thấy, và "không thấy dòng này nghĩa là..." |
| 4 | **Lưu ý** | từng cái bẫy một, mỗi cái mở đầu bằng câu mệnh lệnh in đậm |
| 5 | **Hỏng thì tra ở đây** | bảng *triệu chứng -> nguyên nhân*, tra bằng thứ người ta NHÌN THẤY (dòng log, hành vi sai) |
| 6 | **Tra cứu** | chữ ký, bảng biến môi trường, số đo, số đo hiệu năng — dồn xuống CUỐI |

Quy tắc viết:

- **Mở đầu bằng việc, không phải bằng khái niệm.** Sai: "`@worker` là vòng lặp
  sống mãi với vòng đời do WorkerPool quản lý". Đúng: "Mỗi camera một luồng đọc
  RTSP chạy suốt — viết thế này".
- **Ví dụ phải chạy được nguyên xi.** Thiếu `@injectable` hay thiếu import là
  người ta chép vào rồi ngồi tìm lý do không chạy.
- **Cái bẫy đặt ngay cạnh chỗ người ta sẽ vấp**, không dồn vào một mục "Chú ý"
  ở cuối. `timezone` của `@cron` phải nằm ngay dưới ví dụ `@cron`.
- **Nói cách sửa, đừng chỉ nói cái sai.** "Đừng viết `while True:`" là nửa câu;
  nửa còn lại là đoạn code `while ctx.running:` + `ctx.wait(1)` đặt cạnh nó.
- **Phần "vì sao" giữ lại, nhưng rút xuống một hai câu** và đặt sau phần "làm
  thế nào". Người đọc cần chạy được trước, hiểu sau.
- **Số liệu đo được thì giữ**, nhưng dồn vào mục "Tra cứu" và mở đầu bằng "chỉ
  đọc nếu bạn đang cân nhắc X". Nó là bằng chứng cho một quyết định, không phải
  nội dung chính.
- **Đừng mô tả nội bộ trừ khi người dùng phải làm gì đó khác đi vì nó.** Biết
  `ctx.blocking` dùng pool thread daemon chỉ đáng viết vì nó dẫn tới lời khuyên
  "đặt timeout cho hàm chặn".

Docstring trong `fastapi_modular/**.py` thì ngược lại — ở đó viết cho người
**sửa code**, nên phần "vì sao thiết kế vậy" và chi tiết nội bộ nằm ở đó.

**Mẫu để theo: [`docs/background.md`](docs/background.md).** Mọi trang hướng dẫn
giờ đều có bảng **"Bạn đang cần làm gì?"** ở đầu và **"Hỏng thì tra ở đây"** ở
cuối. Còn hai chỗ chưa xong, làm khi có dịp sửa vào trang đó:

- `websocket.md` và `providers.md` vẫn đánh số mục (`## 5. Phòng`) và để phần
  tra cứu rải ra nhiều mục thay vì gom vào một `## Tra cứu`. Gộp lại sẽ đổi
  neo, nên đừng làm nửa vời — đổi thì đổi luôn cả link trỏ vào.
- `docs/README.md` (mục lục) và `docs/architecture.md` không theo khung này, và
  không cần: chúng không phải trang "làm thế nào".

Toàn bộ quy tắc đóng gói trong skill
[`writing-docs`](.claude/skills/writing-docs/SKILL.md) — viết docs thì gọi nó.

## Quy tắc BẮT BUỘC: sửa code là phải sửa docs

Mỗi thay đổi code phải cập nhật tài liệu tương ứng **trong cùng commit**. Người
đọc tin vào từng con số và từng tên cờ — lệch một chỗ là họ gõ theo rồi lỗi.

| Sửa ở đâu | Bắt buộc soi lại |
|---|---|
| trường trong `core/config.py` (`Settings` và các lớp con) | `docs/config.md` (bảng biến), và doc của nhóm đó: `database.md` / `mongodb.md` / `websocket.md` / `rabbitmq.md` / `redis.md` / `mqtt.md` / `kafka.md` |
| lệnh hoặc cờ trong `cli/` | bảng lệnh ở **`README.md` VÀ `README.vi.md`**, cây `cli/` ở cả hai README **và** `docs/architecture.md` |
| tên file / lớp mà `cli/new_module.py` sinh ra | mục "Thêm module mới" ở `docs/architecture.md` |
| `core/providers.py` hoặc `cli/new_provider.py` | `docs/providers.md` |
| `core/rpc.py`, `*/responders.py`, `emit`/`send` | `docs/rpc.md`, và bảng đối chiếu NestJS ở **cả hai README** + `docs/architecture.md` |
| `infrastructure/database/base.py` (`@entity`, `reference`, khoá ngoại) | `docs/database.md` mục khai báo entity + khoá ngoại, **và `docs/mongodb.md`** (bên đó khung tự áp ràng buộc, khác hẳn), và bảng đối chiếu NestJS ở **cả hai README** + `docs/architecture.md` |
| `infrastructure/database/query.py` (builder) | `docs/database.md` mục truy vấn phức tạp, và bảng đối chiếu NestJS ở **cả hai README** + `docs/architecture.md` |
| `infrastructure/database/mongo.py` | `docs/mongodb.md` — **mọi** mục, nhất là bảng "cái KHÔNG dùng được" |
| transaction (`repository.py`, `sql.py`, `memory.py`) | `docs/database.md` mục Transaction **và** `docs/mongodb.md` mục "Không có transaction" |
| `core/scheduler.py`, `core/cron.py`, `core/jobs.py`, `core/workers.py`, `core/events.py`, `core/locks.py` | `docs/background.md`, bảng nhóm biến ở `docs/config.md`, bảng `groups` trong `tests/test_configure_env.py`, và bảng đối chiếu NestJS ở **cả hai README** |
| API công khai (`fastapi_modular/__init__.py`, decorator, method) | doc của phần đó, và `docs/README.md` nếu đổi bảng đối chiếu |
| thêm/bớt test | con số test ở **cả hai README** (cây thư mục) và `docs/architecture.md` (mục Chất lượng mã) |

Ba cái bẫy đã từng làm docs sai:

1. **Con số viết tay** — "299 test", "78 test", "11 biến". Đo lại trước khi giữ
   nguyên, đừng chép.
2. **Bảng liệt kê** — thêm một biến vào `Settings` hay một lệnh vào CLI mà quên
   thêm dòng vào bảng thì bảng lặng lẽ thiếu, không ai báo.
3. **Số hiệu năng viết theo cảm giác.** Đã hai lần viết một con số vào docs rồi
   mới đo, và cả hai lần sai hơn 3 lần ("1.300.000 lượt/giây" — thật ra 66.000;
   "66.000 -> 96.000" — thật ra 220.000). **Đo trước, viết sau.** Và khi đo,
   kiểm luôn là mình đang đo đúng thứ: một lần cả bảng đo RabbitMQ hoá ra đang
   đo RTT ra Internet vì `.env` trỏ sang server thật, một lần khác thì
   `log.debug` lọt vào phép đo vì structlog dùng `PrintLoggerFactory` nên
   `logging.disable()` không có tác dụng.

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
pytest -q                       # 1134 passed, 358 skipped (358 skip cần hạ tầng hoặc driver thật)
fam lint fastapi_modular src tests    # `fam lint` trần chỉ soi `src`, thiếu thư viện và test
```

Bộ test MongoDB (`tests/test_mongo_query.py`) cần một Mongo thật, và nó đối
chiếu TỪNG CA với backend `memory` — đó là cách duy nhất bắt được chỗ Mongo hiểu
khác SQL:

```bash
TEST_MONGO_DSN='mongodb://root:root@127.0.0.1:27017/?authSource=admin' pytest -q
```

`tests/test_configure_env.py::test_bien_nhac_trong_docs_deu_con_that` đối chiếu
mọi biến `APP_*` trong docs với `Settings` thật. Thêm một **nhóm** biến mới
(`APP_<TÊN>__*`) thì phải thêm nhóm đó vào bảng `nhom` trong chính test, nếu
không biến có thật vẫn bị báo là "không thuộc nhóm nào".

Số test ở đây cũng là con số viết tay — sửa khi nó đổi.

Docs thì không có test tự động cho phần văn xuôi, nhưng ba phép kiểm này bắt
gần hết lỗi và chạy trong vài giây — **viết script rồi chạy, đừng đọc tay**:

1. **Link và neo** — mọi `[x](y.md#z)` trong `docs/` phải trỏ tới file có thật
   và tiêu đề có thật. Slug của GitHub thay **từng** khoảng trắng bằng `-`,
   KHÔNG gộp nhiều khoảng trắng thành một.
2. **Import trong code block** — trích mọi `from fastapi_modular... import X`
   rồi `importlib` + `hasattr` thật.
3. **Kwarg trong code block** — bắt mọi `ast.Call`, so tên tham số với
   `inspect.signature` thật. Đây là phép bắt được nhiều nhất khi vừa đổi API.
4. **Chạy thật khối ví dụ** — dựng backend tạm rồi `exec` từng khối `.query()`
   trong `database.md` (sqlite) và `mongodb.md` (Mongo thật). Phép này bắt được
   thứ ba phép trên không thấy: ví dụ dùng cột mà entity trong CHÍNH trang đó
   không có. Bỏ qua khối chứa `self.` (đoạn trong service), `LỖI` (khối cố ý
   sai) và `assert` (khối "kiểm xem chạy chưa", nói về dữ liệu của người đọc).
5. **Method công khai nào chưa được docs nhắc** — duyệt `vars(Query)` /
   `vars(Repository)` và tìm trong docs. Bắt được ca "thêm method mới rồi quên
   viết hướng dẫn".

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
