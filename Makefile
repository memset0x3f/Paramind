PYTHON ?= python

.PHONY: help install test-inference test-scheduler test-p2p test-backend local-demo

help:
	@echo "ParaMind root workflow (Python)"
	@echo ""
	@echo "  make install         # $(PYTHON) -m pip install -r requirements.txt"
	@echo "  make test-inference  # $(PYTHON) -m pytest test/inference -v"
	@echo "  make test-scheduler  # $(PYTHON) -m pytest test/scheduler -v"
	@echo "  make test-p2p        # $(PYTHON) -m pytest test/p2p/test_p2p.py -v"
	@echo "  make test-backend    # $(PYTHON) -m pytest test/backend -v"
	@echo "  make local-demo      # $(PYTHON) scripts/local_demo.py"

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

local-demo:
	$(PYTHON) scripts/local_demo.py
