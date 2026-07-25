PYTHON_ARTIFACTS = $(ARTIFACTS_DIR)

.PHONY: build-ApiFunction build-IngestionFunction build-python

build-ApiFunction: build-python

build-IngestionFunction: build-python

build-python:
	test -n "$(ARTIFACTS_DIR)"
	uv export --frozen --no-dev --group aws --no-emit-project \
		--format requirements-txt | \
		uv pip install --python-version 3.14 --python-platform x86_64-manylinux_2_28 \
		--target "$(PYTHON_ARTIFACTS)" --no-deps --requirements -
	cp -R src/fpl_data_relay "$(PYTHON_ARTIFACTS)/fpl_data_relay"
