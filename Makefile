PYTHON ?= python

.PHONY: help install test-inference test-backend local-demo p2p-demo

help:
	@echo "ParaMind root workflow (Python)"
	@echo ""
	@echo "  make install         # $(PYTHON) -m pip install -r requirements.txt"
	@echo "  make test-inference  # $(PYTHON) -m pytest test/inference -v"
	@echo "  make test-backend    # $(PYTHON) -m pytest test/backend -v"
	@echo "  make local-demo      # $(PYTHON) scripts/local_demo.py"
	@echo "  make p2p-demo        # $(PYTHON) main.py"

install:
	$(PYTHON) -m pip install -r requirements.txt

test-inference:
	$(PYTHON) -m pytest test/inference -v

test-backend:
	$(PYTHON) -m pytest test/backend -v

local-demo:
	$(PYTHON) scripts/local_demo.py

p2p-demo:
	$(PYTHON) main.py
