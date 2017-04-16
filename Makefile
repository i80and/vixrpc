.PHONY: test lint

test:

lint:
	pep8 --max-line-length=120 vixrpcgen.py
	mypy --strict-optional --ignore-missing-imports vixrpcgen.py
