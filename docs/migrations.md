# Migration với Alembic

Chỉ dùng cho **SQLite và PostgreSQL**. MongoDB không có schema cố định nên không
cần migration — xem [mongodb.md](mongodb.md#khai-báo-entity).

## Vì sao cần, khi đã có `schema_mode=sync`

`sync` thêm và xoá được cột, nhưng:

- **Không đổi được kiểu cột** — chỉ cảnh báo.
- **Không phân biệt đổi tên với xoá+thêm** — đổi tên field là mất sạch dữ liệu cột cũ.
- **Không có lịch sử, không rollback, không review.**
- Hai tiến trình cùng khởi động có thể chạy DDL đồng thời.

Nên: `sync` cho lúc đang phát triển, Alembic cho production.

## Cài

Alembic nằm sẵn trong extra `sqlite` và `postgres`:

```bash
fam install postgres     # hoặc: fam install sqlite
```

## Ba lệnh dùng hằng ngày

```bash
fam migrate create -m "them cot phone cho user"   # sinh migration từ thay đổi entity
fam migrate                                       # chạy lên bản mới nhất
fam migrate down                                  # lùi lại một bản
```

Thêm:

```bash
fam migrate history      # lịch sử và bản đang áp dụng
fam migrate sql          # in câu SQL thay vì chạy, để DBA duyệt trước
```

## Luồng làm việc

**1. Sửa entity**

```python
@entity(unique=["email"])
@dataclass(slots=True)
class User:
    ...
    phone: str = ""          # trường mới
```

**2. Sinh migration**

```bash
$ fam migrate create -m "them cot phone cho user"
INFO  [alembic.autogenerate.compare.tables] Detected added column 'users.phone'
Generating migrations/versions/20260821_1020_them_cot_phone_cho_user.py ... done
```

**3. Đọc lại file sinh ra.** Autogenerate đoán, không phải lúc nào cũng đúng —
đặc biệt với đổi tên cột, nó sẽ sinh ra `drop_column` + `add_column`, tức mất dữ
liệu. Đổi tên thì sửa tay thành `op.alter_column(..., new_column_name=...)`.

**4. Chạy**

```bash
$ fam migrate
INFO  [alembic.runtime.migration] Running upgrade cda85f1da43c -> 17f5a351aa07, them cot phone cho user
```

Dữ liệu cũ giữ nguyên, cột mới nhận `NULL` — và đọc ra qua Repository sẽ rơi về
giá trị mặc định của entity (xem [database.md](database.md#thêm-trường)).

**5. Prod: tắt tự chỉnh schema**

```dotenv
APP_DB__SCHEMA_MODE=off
```

App sẽ **soi và cảnh báo** nếu thiếu index đã khai báo ở `@entity`, vì ở chế độ
này index phải nằm trong migration:

```
db.indexes_missing  indexes=['devices(owner_id, status)', 'users(email) (UNIQUE)']
```

## Hai điểm khác bản mẫu của Alembic

**DSN lấy từ `.env`, không viết trong `alembic.ini`.** Nhờ vậy migration và app
luôn chạy trên cùng một database, không có chuyện migrate nhầm chỗ.

**`target_metadata` dựng từ chính dataclass entity** qua `build_metadata()`, nên
autogenerate so được entity với database mà entity vẫn không dính ORM.

`migrations/env.py` cũng gọi `load_all_modules()` để mọi `@entity` kịp đăng ký —
Alembic không đi qua đường khởi động app nên không tự thấy chúng.

## SQLite cần chế độ batch

SQLite không có `ALTER TABLE ... ALTER COLUMN`. Alembic lách bằng cách tạo bảng
mới rồi chép dữ liệu sang; `env.py` tự bật `render_as_batch` khi driver là
sqlite, nên bạn không phải làm gì.

## Bắt đầu với database đã có sẵn dữ liệu

Nếu bảng đã tồn tại (do `schema_mode=create` tạo trước đó), đừng chạy migration
đầu tiên — nó sẽ đòi tạo lại bảng. Đánh dấu là đã áp dụng:

```bash
.venv/bin/python -m alembic stamp head
```

Từ đó trở đi dùng `fam migrate create` như bình thường.
