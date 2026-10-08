.PHONY: run run-debug lint format type-check test clean install install-all

run:
	python3 -m rax_disp_logic_optimised.cli.main

run-debug:
	python3 -m rax_disp_logic_optimised.cli.main --debug

lint:
	ruff check rax_disp_logic_optimised/ tests/

format:
	ruff format rax_disp_logic_optimised/ tests/

type-check:
	mypy rax_disp_logic_optimised/

test:
	python3 -m pytest tests/ -v

install:
	pip install -e .

install-all:
	pip install -e ".[all]"

clean:
	rm -rf build/ dist/ *.egg-info .ruff_cache .mypy_cache
	find . -type d -name __pycache__ -exec rm -rf {} +
