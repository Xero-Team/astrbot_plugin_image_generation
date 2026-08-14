.PHONY: format check quality test check-py check-yaml check-plugin-files check-contract quality-security quality-complexity

PYTHON ?= python
RUFF := $(PYTHON) -m ruff
PYTEST := $(PYTHON) -m pytest
BANDIT := $(PYTHON) -m bandit
PIP_AUDIT := $(PYTHON) -m pip_audit
ASTRBOT_ROOT ?= ../AstrBot
ASTRBOT_PLUGIN_CHECKER := $(ASTRBOT_ROOT)/.agents/skills/create-astrbot-plugin/scripts/check_plugin.py
PYTHON_TARGETS := adapter core main.py

format:
	@$(RUFF) format .
	@$(RUFF) check --fix .

check: check-py check-yaml test check-plugin-files

check-py:
	@$(RUFF) format --check .
	@$(RUFF) check .
	@$(PYTHON) -m compileall -q $(PYTHON_TARGETS) tests

check-yaml:
	@$(PYTHON) -m yamllint --strict metadata.yaml .github

test:
	@$(PYTEST) tests -q

check-plugin-files:
	@$(PYTHON) scripts/check_plugin_files.py

check-contract:
	@test -f "$(ASTRBOT_PLUGIN_CHECKER)" || { echo "AstrBot checker not found: $(ASTRBOT_PLUGIN_CHECKER)" >&2; exit 2; }
	@$(PYTHON) "$(ASTRBOT_PLUGIN_CHECKER)" --astrbot-root "$(ASTRBOT_ROOT)" .

quality: check quality-security quality-complexity

quality-security:
	@$(BANDIT) -ll -ii -r $(PYTHON_TARGETS) -c pyproject.toml
	@$(PIP_AUDIT) --strict -r requirements.txt

quality-complexity:
	@$(RUFF) check --select C901 --config "lint.mccabe.max-complexity=35" $(PYTHON_TARGETS)
