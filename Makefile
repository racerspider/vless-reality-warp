.PHONY: test check
test:
	python3 -m unittest discover -s tests -v
	sh -n compose.sh scripts/warp-health.sh

check: test
	python3 scripts/check-public.py
