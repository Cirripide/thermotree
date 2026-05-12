ARCH := $(shell uname -m)

ifeq ($(ARCH), arm64)
    PLATFORM_FLAG := linux/amd64
else
    PLATFORM_FLAG := linux/x86_64
endif

NODE_VERSION := $(shell cat frontend/.nvmrc)

dev:
	@echo "Architecture: $(ARCH)"
	@echo "Platform: $(PLATFORM_FLAG)"
	@echo "Starting with node version: $(NODE_VERSION)"
	DOCKER_PLATFORM=$(PLATFORM_FLAG) NODE_VERSION=$(NODE_VERSION) docker-compose up --build

down:
	docker-compose down
