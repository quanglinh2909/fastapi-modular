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

# Makefile này chỉ là lối tắt cho `pym` khi làm việc TRONG repo. Người dùng thư
# viện không có nó, nên mọi lệnh đều phải chạy được qua `pym` — và cách chắc
# chắn nhất để giữ điều đó đúng là ở đây không tự viết gì cả, chỉ gọi lại.
PYM := $(PY) -m pymodular.cli.main

dev: ## Chạy API kèm autoreload
	$(PYM) dev

run: ## Chạy API chế độ production (nhiều worker — cần driver DB thật)
	$(PYM) run --workers $(or $(WORKERS),4)

module: ## Sinh khung module mới — dùng: make module name=alerts [entity=alert] [ws=1] [mq=1]
	@test -n "$(name)" || { echo 'Thiếu tên. Dùng: make module name=alerts'; exit 1; }
	$(PYM) module $(name) $(if $(entity),--entity $(entity),) $(if $(ws),--gateway,) $(if $(mq),--consumer,)

gateway: ## Thêm gateway WebSocket vào module đã có — dùng: make gateway name=alerts
	@test -n "$(name)" || { echo 'Thiếu tên. Dùng: make gateway name=alerts'; exit 1; }
	$(PYM) module $(name) --gateway-only $(if $(entity),--entity $(entity),)

consumer: ## Thêm consumer RabbitMQ vào module đã có — dùng: make consumer name=alerts
	@test -n "$(name)" || { echo 'Thiếu tên. Dùng: make consumer name=alerts'; exit 1; }
	$(PYM) module $(name) --consumer-only $(if $(entity),--entity $(entity),)

migrate: ## Chạy migration lên bản mới nhất
	$(PYM) migrate up

migrate-create: ## Sinh migration từ thay đổi entity — dùng: make migrate-create m="them cot phone"
	@test -n "$(m)" || { echo 'Thiếu tên. Dùng: make migrate-create m="mô tả thay đổi"'; exit 1; }
	$(PYM) migrate create -m "$(m)"

migrate-down: ## Lùi lại một bản
	$(PYM) migrate down

migrate-history: ## Lịch sử migration và bản đang áp dụng
	@$(PYM) migrate history

migrate-sql: ## In câu SQL thay vì chạy (để DBA duyệt trước)
	$(PYM) migrate sql

lint: ## Soi lỗi tĩnh
	$(PYM) lint pymodular src tests

lint-fix: ## Soi và tự sửa những lỗi sửa được
	$(PYM) lint --fix pymodular src tests

test: ## Chạy test
	$(PYM) test

install: ## Cài khung ở chế độ chỉnh sửa được (chưa có driver database)
	$(PIP) install -e .

install-dev: ## Cài kèm công cụ phát triển (pytest, httpx, ruff, build, twine)
	$(PIP) install -e .
	$(PYM) install dev
	$(PIP) install build twine

install-sqlite: ## Cài driver SQLite + ghi biến vào .env
	$(PYM) install sqlite

install-postgres: ## Cài driver PostgreSQL + ghi biến vào .env
	$(PYM) install postgres

install-mongo: ## Cài driver MongoDB + ghi biến vào .env
	$(PYM) install mongodb

install-redis: ## Cài Redis (cache, đếm, pub/sub) + ghi biến vào .env
	$(PYM) install redis

install-ws-redis: ## Bật adapter Redis cho WebSocket nhiều worker + ghi biến vào .env
	$(PYM) install ws-redis

install-rabbitmq: ## Cài client RabbitMQ + ghi biến vào .env
	$(PYM) install rabbitmq

install-mqtt: ## Cài client MQTT + ghi biến vào .env
	$(PYM) install mqtt

install-kafka: ## Cài client Kafka + ghi biến vào .env
	$(PYM) install kafka







info: ## Đang nối vào đâu, thư viện nào đã cài
	@$(PYM) info

build: ## Dựng wheel + sdist vào dist/
	$(PYM) build

publish-test: build ## Đẩy lên TestPyPI (thử trước khi đẩy thật)
	$(PYM) publish --test

publish: build ## Đẩy lên PyPI
	$(PYM) publish

clean: ## Xoá cache và bản dựng
	$(PYM) clean