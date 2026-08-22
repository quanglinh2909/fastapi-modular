"""Nơi chứa các module nghiệp vụ.

Mỗi thư mục con ở đây là một module độc lập (giống @Module của NestJS):
tự khai báo router, service, repository, schema của riêng mình và chỉ lộ ra
biến `router` trong __init__.py. `app/app.py` sẽ tự quét và gắn chúng vào
app — thêm module mới không cần sửa file nào ở ngoài.
"""
