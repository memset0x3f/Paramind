PYTHON ?= python
DESKTOP_DIR := /Users/acropolis/Github_Project/Paramind/paramind/apps/desktop

.PHONY: help install test-inference test-scheduler test-p2p test-backend local-demo p2p-demo test-frontend-fast test-frontend-smoke open-frontend-harness

help:
	@echo "ParaMind root workflow (Python)"
	@echo ""
	@echo "  make install               # $(PYTHON) -m pip install -r requirements.txt"
	@echo "  make test-inference        # $(PYTHON) -m pytest test/inference -v"
	@echo "  make test-scheduler        # $(PYTHON) -m pytest test/scheduler -v"
	@echo "  make test-p2p              # $(PYTHON) -m pytest test/p2p/test_p2p.py -v"
	@echo "  make test-backend          # $(PYTHON) -m pytest test/backend -v"
	@echo "  make test-frontend-fast    # renderer state + harness + Playwright (no Electron)"
	@echo "  make test-frontend-smoke   # desktop backend smoke boundary checks"
	@echo "  make open-frontend-harness # serve browser harness at http://127.0.0.1:4173/dev_harness.html?harness=1"
	@echo "  make local-demo            # $(PYTHON) scripts/local_demo.py"
	@echo "  make p2p-demo              # $(PYTHON) main.py"

install:
	$(PYTHON) -m pip install -r requirements.txt

test-inference:
	$(PYTHON) -m pytest test/inference -v -k "not llama"

test-scheduler:
	$(PYTHON) -m pytest test/scheduler -v

test-p2p:
	$(PYTHON) -m pytest test/p2p/test_p2p.py -v

test-backend:
	$(PYTHON) -m pytest test/backend -v

test-frontend-fast:
	cd $(DESKTOP_DIR) && npm run test:frontend-fast

test-frontend-smoke:
	cd $(DESKTOP_DIR) && uv run pytest -q --tb=short /Users/acropolis/Github_Project/Paramind/test/backend/test_api.py /Users/acropolis/Github_Project/Paramind/test/backend/test_inference_runner.py

open-frontend-harness:
	@echo "Serving renderer harness at http://127.0.0.1:4173/dev_harness.html?harness=1"
	cd $(DESKTOP_DIR) && npm run harness:serve

local-demo:
	$(PYTHON) scripts/local_demo.py

p2p-demo:
	$(PYTHON) main.py
