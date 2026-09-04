PYTHON ?= python3

.PHONY: test check

test:
	$(PYTHON) -m unittest discover -s tests

check: test
	node --check version_compare/static/app.js
	$(PYTHON) -m py_compile version_compare/*.py
