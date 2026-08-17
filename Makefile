.PHONY: install test eval demo verify

install:
	python3 -m pip install -e .

test:
	PYTHONPATH=src python3 -m unittest discover -s tests -v

eval:
	PYTHONPATH=src python3 -m feedverdict eval

demo:
	PYTHONPATH=src python3 -m feedverdict demo stale

verify: test eval
	python3 -m compileall -q src tests
