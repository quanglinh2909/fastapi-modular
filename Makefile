VENV := .venv
PY   := $(VENV)/bin/python
PIP  := $(PY) -m pip

# Nạp .env để make dùng chung biến với app (pydantic Settings cũng đọc file này).
-include .env
export

APP_HOST ?= 0.0.0.0
APP_PORT ?= 8000

.DEFAULT_GOAL := help
.PHONY: help dev run test lint lint-fix module gateway consumer migrate migrate-create migrate-down migrate-history migrate-sql install install-dev install-sqlite install-postgres install-mongo install-redis install-ws-redis install-rabbitmq install-mqtt install-kafka info build publish publish-test clean

help: ## Danh sách lệnh
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# Makefile này chỉ là lối tắt cho `fam` khi làm việc TRONG repo. Người dùng thư
# viện không có nó, nên mọi lệnh đều phải chạy được qua `fam` — và cách chắc
# chắn nhất để giữ điều đó đúng là ở đây không tự viết gì cả, chỉ gọi lại.
FAM := $(PY) -m fastapi_modular.cli.main

dev: ## Chạy API kèm autoreload
	$(FAM) dev

run: ## Chạy API chế độ production (nhiều worker — cần driver DB thật)
	$(FAM) run --workers $(or $(WORKERS),4)

module: ## Sinh khung module mới — dùng: make module name=alerts [entity=alert] [ws=1] [mq=1]
	@test -n "$(name)" || { echo 'Thiếu tên. Dùng: make module name=alerts'; exit 1; }
	$(FAM) module $(name) $(if $(entity),--entity $(entity),) $(if $(ws),--gateway,) $(if $(mq),--consumer,)

gateway: ## Thêm gateway WebSocket vào module đã có — dùng: make gateway name=alerts
	@test -n "$(name)" || { echo 'Thiếu tên. Dùng: make gateway name=alerts'; exit 1; }
	$(FAM) module $(name) --gateway-only $(if $(entity),--entity $(entity),)

consumer: ## Thêm consumer RabbitMQ vào module đã có — dùng: make consumer name=alerts
	@test -n "$(name)" || { echo 'Thiếu tên. Dùng: make consumer name=alerts'; exit 1; }
	$(FAM) module $(name) --consumer-only $(if $(entity),--entity $(entity),)

migrate: ## Chạy migration lên bản mới nhất
	$(FAM) migrate up

migrate-create: ## Sinh migration từ thay đổi entity — dùng: make migrate-create m="them cot phone"
	@test -n "$(m)" || { echo 'Thiếu tên. Dùng: make migrate-create m="mô tả thay đổi"'; exit 1; }
	$(FAM) migrate create -m "$(m)"

migrate-down: ## Lùi lại một bản
	$(FAM) migrate down

migrate-history: ## Lịch sử migration và bản đang áp dụng
	@$(FAM) migrate history

migrate-sql: ## In câu SQL thay vì chạy (để DBA duyệt trước)
	$(FAM) migrate sql

lint: ## Soi lỗi tĩnh
	$(FAM) lint fastapi_modular src tests

lint-fix: ## Soi và tự sửa những lỗi sửa được
	$(FAM) lint --fix fastapi_modular src tests

test: ## Chạy test
	$(FAM) test

install: ## Cài khung ở chế độ chỉnh sửa được (chưa có driver database)
	$(PIP) install -e .

install-dev: ## Cài kèm công cụ phát triển (pytest, httpx, ruff, build, twine)
	$(PIP) install -e .
	$(FAM) install dev
	$(PIP) install build twine

install-sqlite: ## Cài driver SQLite + ghi biến vào .env
	$(FAM) install sqlite

install-postgres: ## Cài driver PostgreSQL + ghi biến vào .env
	$(FAM) install postgres

install-mongo: ## Cài driver MongoDB + ghi biến vào .env
	$(FAM) install mongodb

install-redis: ## Cài Redis (cache, đếm, pub/sub) + ghi biến vào .env
	$(FAM) install redis

install-ws-redis: ## Bật adapter Redis cho WebSocket nhiều worker + ghi biến vào .env
	$(FAM) install ws-redis

install-rabbitmq: ## Cài client RabbitMQ + ghi biến vào .env
	$(FAM) install rabbitmq

install-mqtt: ## Cài client MQTT + ghi biến vào .env
	$(FAM) install mqtt

install-kafka: ## Cài client Kafka + ghi biến vào .env
	$(FAM) install kafka







info: ## Đang nối vào đâu, thư viện nào đã cài
	@$(FAM) info

build: ## Dựng wheel + sdist vào dist/
	$(FAM) build

publish-test: build ## Đẩy lên TestPyPI (thử trước khi đẩy thật)
	$(FAM) publish --test

publish: build ## Đẩy lên PyPI
	$(FAM) publish

clean: ## Xoá cache và bản dựng
	$(FAM) clean