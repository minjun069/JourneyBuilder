PYTHON ?= python3
NODE_IMAGE ?= node:22-bookworm

.PHONY: g1-check g1-foundation g1-python g1-ai g1-backend g1-rag g1-tools g1-observability g1-security g1-node g1-runtime f14-regression

g1-check: g1-foundation g1-python g1-node

g1-foundation:
	$(PYTHON) g1_gate.py

g1-python: g1-ai g1-backend g1-rag g1-tools g1-observability g1-security

g1-ai:
	$(PYTHON) -m unittest discover -s ai-pjt/tests -v

g1-backend:
	$(PYTHON) -m unittest discover -s backend-pjt/tests -v
	$(PYTHON) -m unittest discover -s backend-pjt/contract_tests -v

g1-rag:
	cd ai-pjt && $(PYTHON) -m unittest discover -s rag/tests -v

g1-tools:
	cd ai-pjt && $(PYTHON) -m unittest discover -s tools/tests -v

g1-observability:
	cd ai-pjt && $(PYTHON) -m unittest discover -s evals/tests -v

g1-security:
	cd ai-pjt/evals/security && $(PYTHON) -m unittest -v test_security_regression.py

g1-node:
	docker run --rm --mount type=bind,src=$(CURDIR),dst=/workspace,readonly -w /workspace/front-pjt $(NODE_IMAGE) npm test
	docker run --rm --mount type=bind,src=$(CURDIR),dst=/workspace,readonly -w /workspace $(NODE_IMAGE) node --test front-pjt/tests/e2e/p07/p07-preparation.test.mjs

g1-runtime:
	$(PYTHON) g1_runtime.py

f14-regression:
	bash deploy/f14-regression.sh
