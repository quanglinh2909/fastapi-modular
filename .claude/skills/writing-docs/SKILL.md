---
name: writing-docs
description: Viết hoặc sửa một trang trong docs/ của fastapi-modular theo chuẩn hướng-dẫn-dùng (mẫu docs/background.md), kèm 5 phép kiểm tự động trước khi nói xong.
---

# Viết docs cho fastapi-modular

Người đọc là **người chưa biết dùng tính năng đó**, mở trang với câu hỏi "tôi
muốn làm X thì viết thế nào" — không phải "cơ chế bên trong ra sao". Mẫu chuẩn
để đối chiếu: `docs/background.md`.

## Khung trang, đúng thứ tự này

| # | Phần | Nội dung |
|---|---|---|
| 1 | **Bạn đang cần làm gì?** | bảng "việc muốn làm" -> link mục. Câu chữ lấy từ miệng người dùng ("cứ 5 giây kiểm tra camera"), không phải tên kỹ thuật |
| 2 | **Làm thế nào** | ví dụ chép-dán-chạy được: đủ import, đủ `@injectable`, ghi rõ file đặt ở đâu |
| 2b | **Tham số** | ngay dưới ví dụ, MỘT bảng cho mỗi loại: `Tham số \| Bắt buộc \| Mặc định \| Để làm gì`. Đừng dồn tham số của sáu loại vào một bảng ở cuối trang — người đọc đang ở mục nào thì cần bảng của mục đó |
| 3 | **Kiểm xem nó chạy chưa** | dòng log phải thấy, và "không thấy dòng này nghĩa là..." |
| 4 | **Lưu ý** | từng cái bẫy một, mỗi cái mở đầu bằng câu mệnh lệnh in đậm, đặt NGAY CẠNH chỗ người ta sẽ vấp |
| 5 | **Hỏng thì tra ở đây** | bảng *triệu chứng -> nguyên nhân*, tra bằng thứ người ta NHÌN THẤY |
| 6 | **Tra cứu** | chữ ký, bảng biến môi trường, số đo — dồn xuống CUỐI |

## Quy tắc viết

- Mở đầu bằng VIỆC, không phải khái niệm. Sai: "`@worker` là vòng lặp có vòng
  đời do WorkerPool quản lý". Đúng: "Mỗi camera một luồng đọc RTSP chạy suốt —
  viết thế này".
- Ví dụ phải chạy nguyên xi — thiếu một import là người ta chép vào rồi ngồi
  tìm lý do không chạy.
- Nói cách sửa, đừng chỉ nói cái sai: "đừng `while True:`" phải kèm đoạn
  `while ctx.running:` đặt cạnh.
- "Vì sao" giữ lại nhưng rút còn một hai câu, đặt SAU "làm thế nào".
- Số đo được thì giữ, dồn vào Tra cứu; **đo trước, viết sau** — đã hai lần viết
  số theo cảm giác và cả hai lần sai hơn 3 lần.
- Đừng mô tả nội bộ trừ khi người dùng phải làm gì đó khác đi vì nó.
- Con số viết tay (số test, số biến) phải đo lại trước khi giữ nguyên.
- **Mặc định trong bảng tham số phải đọc từ `inspect.signature`, không gõ tay.**
  Viết script so bảng với chữ ký thật rồi chạy — nó đã bắt được một chỗ lệch
  ngay lần đầu.
- **Đừng dựa vào neo trùng tên** (`#tham-số-3` sinh ra từ sáu mục cùng tên
  "Tham số"): thêm một mục nữa là mọi số dịch đi trong im lặng. Đặt tiêu đề
  riêng — "Tham số của `@job`".
- Sửa trang cũ thì chuyển trang đó sang khung này; đừng viết trang mới theo lối cũ.

## Trước khi nói "xong": 5 phép kiểm, viết script mà chạy

1. **Link + neo**: mọi `[x](y.md#z)` trỏ tới file thật, tiêu đề thật. Slug
   GitHub thay TỪNG khoảng trắng bằng `-`, không gộp.
2. **Import trong code block**: trích mọi `from fastapi_modular... import X`
   rồi `importlib` + `hasattr` thật.
3. **Kwarg trong code block**: bắt mọi `ast.Call`, so với `inspect.signature`.
4. **Chạy thật khối ví dụ**: dựng backend tạm rồi `exec` từng khối (sqlite cho
   `database.md`, Mongo container local cho `mongodb.md`). Bỏ khối chứa
   `self.`, `LỖI`, `assert`.
5. **Method công khai chưa được nhắc**: duyệt `vars(...)` của lớp public rồi
   tìm trong docs.

Ngoài ra `tests/test_configure_env.py::test_bien_nhac_trong_docs_deu_con_that`
đối chiếu mọi biến `APP_*` trong docs với `Settings` thật — chạy nó sau khi đổi
bảng biến.

## Việc kéo theo (cùng commit)

- Đổi bảng lệnh CLI / bảng đối chiếu NestJS / con số -> soi cả `README.md` VÀ
  `README.vi.md` (hai bản phải tương đương, cùng thứ tự mục).
- Link trong `README.md` phải là URL tuyệt đối (PyPI); link trong `docs/*.md`
  để tương đối.
