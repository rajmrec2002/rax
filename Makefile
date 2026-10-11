.PHONY: run run-debug server lint format type-check test clean install install-all

run:
	python3 -m rax_disp_logic_optimised.cli.main

run-debug:
	python3 -m rax_disp_logic_optimised.cli.main --debug

server:
	python3 -m rax_disp_logic_optimised.server --host 127.0.0.1 --port 8765

lint:
	ruff check rax_disp_logic_optimised/

format:
	ruff format rax_disp_logic_optimised/

type-check:
	mypy rax_disp_logic_optimised/

test:
	python3 -m pytest rax_disp_logic_optimised/tests -v

install:
	pip install -e ".[gui]"

install-all:
	pip install -e ".[all]"

clean:
	rm -rf build/ dist/ *.egg-info .ruff_cache .mypy_cache
	find . -type d -name __pycache__ -exec rm -rf {} +
