
.PHONY: build
build:
	docker build --tag=camptocamp/sharedconfigmanager-operator docker

.PHONY: build-test
build-test:
	docker build --target=test --tag=camptocamp/sharedconfigmanager-operator-test docker

.PHONY: prospector
prospector: build-test
	docker run camptocamp/sharedconfigmanager-operator-test prospector --output=pylint operator.py

.PHONY: tests
tests:
	pytest --verbose
