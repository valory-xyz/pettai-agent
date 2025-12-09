OPEN_AEA_REPO_PATH := "${OPEN_AEA_REPO_PATH}"
DEPLOYMENT_TYPE := "${DEPLOYMENT_TYPE}"
SERVICE_ID := "${SERVICE_ID}"
PLATFORM_STR := $(shell uname)

# Binary build metadata
RUNNER_NAME := agent_runner_bin
DIST_PATH := dist/$(RUNNER_NAME)
PYINSTALLER_ENTRY := pyinstaller/agent_runner_entry.py
PYINSTALLER_DEPS_SCRIPT := get_pyinstaller_dependencies.py
PYINSTALLER_BIN := $(RUNNER_NAME)
PYINSTALLER_CMD := pyinstaller
AUTONOMY_CMD := autonomy
PACKAGES_FILE := packages/packages.json
HASH_FILE := hash_id
AGENT_ID_FILE := agent_id
AGENT_DIR := ./agent
FRONTEND_DIR := frontend
FRONTEND_BUILD_DIR := $(FRONTEND_DIR)/build
FRONTEND_BUILD_CMD := yarn --cwd $(FRONTEND_DIR)
CHECK_TIMEOUT ?= 30
CHECK_COMMAND ?= ./dist/$(RUNNER_NAME) --version
CHECK_SEARCH_STRING ?= Pett Agent Runner

export CHECK_TIMEOUT
export CHECK_COMMAND
export CHECK_SEARCH_STRING

# Convenience clean target that runs all sub-cleaners
.PHONY: clean
clean: clean-test clean-pyc clean-docs clean-build

# Remove build/dist artifacts and temp files
.PHONY: clean-build
clean-build:
	rm -fr build/
	rm -fr dist/
	rm -fr .eggs/
	rm -fr pip-wheel-metadata
	find . -name '*.egg-info' -exec rm -fr {} +
	find . -name '*.egg' -exec rm -fr {} +
	find . -type d -name __pycache__ -exec rm -rv {} +
	rm -fr Pipfile.lock
	rm -rf plugins/*/build 2>/dev/null || true
	rm -rf plugins/*/dist 2>/dev/null || true

# Remove built documentation
.PHONY: clean-docs
clean-docs:
	rm -fr site/

# Remove python caches
.PHONY: clean-pyc
clean-pyc:
	find . -name '*.pyc' -exec rm -f {} +
	find . -name '*.pyo' -exec rm -f {} +
	find . -name '*~' -exec rm -rf {} +
	find . -name '__pycache__' -exec rm -fr {} +
	find . -name '.DS_Store' -exec rm -fr {} +

# Drop testing artefacts and caches
.PHONY: clean-test
clean-test: clean-cache
	rm -fr .tox/
	rm -f .coverage
	find . -name ".coverage*" -not -name ".coveragerc" -exec rm -fr "{}" \;
	rm -fr coverage.xml
	rm -fr htmlcov/
	find . -name 'log.txt' -exec rm -fr {} +
	find . -name 'log.*.txt' -exec rm -fr {} +

# removes various cache files
.PHONY: clean-cache
clean-cache:
	find . -type d -name .hypothesis -prune -exec rm -rf {} \;
	rm -fr .pytest_cache
	rm -fr .mypy_cache/

# safety: checks dependencies for known security vulnerabilities
# bandit: security linter
.PHONY: security
security:
	tox -p -e safety -e bandit
	gitleaks detect --report-format json --report-path leak_report

# generate abci docstrings
# check copyright
# generate latest hashes for updated packages
# generate docs for updated packages
# fix hashes in docs
.PHONY: generators
generators: clean-cache
	tox -e abci-docstrings
	tox -e fix-copyright
	tox -e lock-packages
	tox -e generate-api-documentation
	tox -e fix-doc-hashes

# Group of fast checks
.PHONY: common-checks-1
common-checks-1:
	tox -p -e check-copyright -e check-hash -e check-packages

# Additional check group
.PHONY: common-checks-2
common-checks-2:
	tox -e check-api-docs
	tox -e check-abci-docstrings
	tox -e check-abciapp-specs
	tox -e check-handlers
	tox -e check-dialogues
	tox -e check-doc-links-hashes

# Run every verification target
.PHONY: all-checks
all-checks: clean security generators common-checks-1 common-checks-2

v := $(shell pip -V | grep virtualenvs)

# Pipenv bootstrap helper
.PHONY: new_env
new_env: clean
	if [ ! -z "$(which svn)" ];\
	then\
		echo "The development setup requires SVN, exit";\
		exit 1;\
	fi;\

	if [ -z "$v" ];\
	then\
		pipenv --rm;\
		pipenv --clear;\
		pipenv --python 3.10;\
		pipenv install --dev --skip-lock;\
		pipenv run pip install -e .[all];\
		echo "Enter virtual environment with all development dependencies now: 'pipenv shell'.";\
	else\
		echo "In a virtual environment! Exit first: 'exit'.";\
	fi

protolint_install:
	mkdir protolint_install
	cd protolint_install && \
		wget https://github.com/yoheimuta/protolint/releases/download/v0.27.0/protolint_0.27.0_Linux_x86_64.tar.gz && \
		tar -xvf protolint_0.27.0_Linux_x86_64.tar.gz && \
		sudo mv protolint /usr/local/bin/protolint
	sudo rm -rf protolint_install

protolint_install_darwin:
	mkdir protolint_install
	cd protolint_install && \
		wget https://github.com/yoheimuta/protolint/releases/download/v0.27.0/protolint_0.27.0_Darwin_x86_64.tar.gz && \
		tar -xvf protolint_0.27.0_Darwin_x86_64.tar.gz && \
		sudo mv protolint /usr/local/bin/protolint
	sudo rm -rf protolint_install

# TODO: use precompiled binary
protolint_install_win:
	powershell -command '$$env:GO111MODULE="on"; go install github.com/yoheimuta/protolint/cmd/protolint@v0.27.0'

# Install Python deps necessary for builds
.PHONY: install-deps
install-deps: clean-cache
	python -m pip install --upgrade pip setuptools wheel
	python -m pip install -r requirements.txt
	python -m pip install open-autonomy==0.19.11 pyinstaller==6.4.0

# Write the agent IPFS hash to hash_id
$(HASH_FILE): $(PACKAGES_FILE)
	@python -c "import json, pathlib; data=json.load(open('$(PACKAGES_FILE)', encoding='utf-8')); print(next((value for key,value in data.get('dev', {}).items() if key.startswith('agent/')), ''))" > $(HASH_FILE)
	@cat $(HASH_FILE)

# Write the agent identifier to agent_id
$(AGENT_ID_FILE): $(PACKAGES_FILE)
	@python -c "import json, pathlib; data=json.load(open('$(PACKAGES_FILE)', encoding='utf-8')); print(next((key.split('/',1)[1] for key in data.get('dev', {}).keys() if key.startswith('agent/')), ''))" > $(AGENT_ID_FILE)
	@cat $(AGENT_ID_FILE)

# Fetch the agent package into ./agent
$(AGENT_DIR): $(HASH_FILE)
	@if [ ! -d "$(AGENT_DIR)" ]; then \
		$(AUTONOMY_CMD) -s fetch --remote `cat $(HASH_FILE)` --alias agent; \
	fi

# Generate a dev ethereum key in ./agent
$(AGENT_DIR)/ethereum_private_key.txt: $(AGENT_DIR)
	cd $(AGENT_DIR) && $(AUTONOMY_CMD) -s generate-key ethereum
	cd $(AGENT_DIR) && $(AUTONOMY_CMD) -s add-key ethereum ethereum_private_key.txt
	cd $(AGENT_DIR) && $(AUTONOMY_CMD) add-key ethereum ethereum_private_key.txt --connection
	cd $(AGENT_DIR) && $(AUTONOMY_CMD) -s issue-certificates

# Zip archive for release uploads
agent.zip: $(AGENT_DIR)
	python -c "import shutil; shutil.make_archive('agent', 'zip', root_dir='.', base_dir='agent')"

# Tarball archive for release uploads
agent.tar.gz: $(AGENT_DIR)
	python -c "import shutil; shutil.make_archive('agent', 'gztar', root_dir='.', base_dir='agent')"

.PHONY: check-frontend-env
check-frontend-env:
	@echo "Checking frontend environment variables..."
	@cd $(FRONTEND_DIR) && node check-env.js || (echo "❌ Frontend environment check failed. Please fix .env file." && exit 1)

.PHONY: build-frontend
build-frontend: check-frontend-env
	@echo "Building frontend (React Scripts will automatically load .env file)..."
	$(FRONTEND_BUILD_CMD) install
	rm -rf $(FRONTEND_BUILD_DIR)
	$(FRONTEND_BUILD_CMD) build

# Platform-agnostic PyInstaller build. Here we use the -p . flag to include the project root in the PYTHONPATH.
# Also, i did hadd setuptools bc the "jaraco" module was not being included in the default PyInstaller hidden imports (unlike it was seen on the https://github.com/valory-xyz/optimus/blob/main/Makefile)
.PHONY: build-agent-runner
build-agent-runner: install-deps $(AGENT_DIR) build-frontend
	$(PYINSTALLER_CMD) \
	-p . \
	--collect-data eth_account \
		--collect-all agent \
		--collect-all setuptools \
	--hidden-import agent.olas_interface \
	--hidden-import agent.pett_agent \
	--hidden-import agent.decision_engine \
	--hidden-import agent.pett_tools \
	--hidden-import agent.telegram_bot \
	--hidden-import agent.olas_interface \
	--add-data $(FRONTEND_BUILD_DIR):frontend/build \
	$(shell python $(PYINSTALLER_DEPS_SCRIPT)) \
	--onefile $(PYINSTALLER_ENTRY) \
	--name $(RUNNER_NAME)
	./$(DIST_PATH) --version

# macOS build with codesigning. Here we use the -p . flag to include the project root in the PYTHONPATH as well.
.PHONY: build-agent-runner-mac
build-agent-runner-mac: install-deps $(AGENT_DIR) build-frontend
	$(PYINSTALLER_CMD) \
	-p . \
	--collect-data eth_account \
		--collect-all agent \
		--collect-all setuptools \
	--hidden-import agent.olas_interface \
	--hidden-import agent.pett_agent \
	--hidden-import agent.decision_engine \
	--hidden-import agent.pett_tools \
	--hidden-import agent.telegram_bot \
	--add-data $(FRONTEND_BUILD_DIR):frontend/build \
	$(shell python $(PYINSTALLER_DEPS_SCRIPT)) \
	--onefile $(PYINSTALLER_ENTRY) \
	--codesign-identity "${SIGN_ID}" \
	--name $(RUNNER_NAME)
	./$(DIST_PATH) --version

# Sanity check the created binary
.PHONY: check-agent-runner
check-agent-runner:
	python check_agent_runner.py
