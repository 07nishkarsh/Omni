# Bank Agent Orchestrator

> ⚠️ **SIMULATION DISCLAIMER**: This project is a **software simulation** of a
> banking workflow orchestrator. It contains **no real financial integrations,
> no credit-bureau connections, no real banking APIs, and no real customer PII**.
> All external data sources (Notion, Gmail, Slack, core-banking, credit bureaus)
> are **mocked**. No real financial decisions are made at any point.

---

## Overview

`bank-agent-orchestrator` is a Python 3.11 FastAPI application that demonstrates
how multiple AI agents can be orchestrated to process simulated banking
transactions through a structured workflow.

```
Transaction submitted
        │
        ▼
  PENDING ──► UNDER_REVIEW  (Validator applies Policy rules)
                   │
          ┌────────┼─────────────┐
          ▼        ▼             ▼
      NEGOTIATING  REJECTED   ESCALATED
          │
    ┌─────┴──────┐
    ▼            ▼
 APPROVED     REJECTED
```

---

## Project Structure

```
bank-agent-orchestrator/
├── app/
│   ├── __init__.py            # FastAPI app factory
│   ├── config.py              # Pydantic-Settings config loader
│   ├── agents/
│   │   ├── agent_runner.py    # LLM invocation + mock fallback
│   │   ├── underwriter_agent.md
│   │   ├── negotiator_agent.md
│   │   └── compliance_agent.md
│   ├── orchestrator/
│   │   ├── state_machine.py   # Workflow state transitions
│   │   ├── negotiation.py     # Multi-round proposal loop
│   │   └── validator.py       # Policy rule evaluation
│   ├── integrations/
│   │   ├── notion_mcp.py      # Notion API adapter (mocked by default)
│   │   ├── gmail.py           # Gmail notification adapter (mocked)
│   │   ├── slack.py           # Slack notification adapter (mocked)
│   │   └── mock_services.py   # Synthetic data generators
│   ├── models/
│   │   ├── transaction.py     # TransactionContext schema
│   │   ├── proposal.py        # Proposal schema
│   │   └── policy.py         # Policy + PolicyRule schemas
│   └── routes/
│       ├── health.py          # GET /health, GET /health/ready
│       └── orchestration.py   # POST /api/v1/transactions, GET …
└── tests/
    ├── test_health.py
    ├── test_models.py
    └── test_orchestrator.py
```

---

## Quick Start

### 1. Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) or `pip`

### 2. Install dependencies

```bash
# Using uv (recommended)
uv venv .venv --python 3.11
source .venv/bin/activate
uv pip install -e ".[dev]"

# Or using pip
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 3. Configure environment

```bash
cp .env.example .env
# Edit .env with your values (all fields have safe mock defaults)
```

### 4. Run the development server

```bash
uvicorn app:app --reload --host 0.0.0.0 --port 8000
```

Open **http://localhost:8000/docs** for the interactive API explorer.

### 5. Run tests

```bash
pytest -v
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness probe |
| `GET` | `/health/ready` | Readiness probe with mock-toggle status |
| `POST` | `/api/v1/transactions` | Submit a simulated transaction |
| `GET` | `/api/v1/transactions` | List all in-memory transactions |
| `GET` | `/api/v1/transactions/{id}` | Get a specific transaction |

---

## Configuration

All configuration is loaded from environment variables (or `.env`) via
[pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/).

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_API_KEY` | `mock-llm-key` | LLM provider API key |
| `LLM_MODEL` | `gemini-2.0-flash` | Model name |
| `NOTION_TOKEN` | `mock-notion-token` | Notion integration token |
| `NOTION_DATABASE_ID` | `mock-notion-db-id` | Target Notion database |
| `GMAIL_CLIENT_ID` | `mock-*` | Gmail OAuth2 credentials |
| `GMAIL_CLIENT_SECRET` | `mock-*` | Gmail OAuth2 credentials |
| `GMAIL_REFRESH_TOKEN` | `mock-*` | Gmail OAuth2 credentials |
| `SLACK_WEBHOOK_URL` | `https://hooks.slack.com/mock/…` | Slack Incoming Webhook |
| `SLACK_BOT_TOKEN` | `xoxb-mock-token` | Slack Bot Token |
| `USE_MOCK_LLM` | `true` | Skip real LLM calls |
| `USE_MOCK_NOTION` | `true` | Skip real Notion calls |
| `USE_MOCK_GMAIL` | `true` | Skip real Gmail calls |
| `USE_MOCK_SLACK` | `true` | Skip real Slack calls |
| `APP_ENV` | `development` | Runtime environment |
| `LOG_LEVEL` | `INFO` | Structured log level |

---

## Agents

| Agent | Prompt File | Role |
|-------|------------|------|
| Underwriter | `underwriter_agent.md` | Evaluates applications against synthetic policy |
| Negotiator | `negotiator_agent.md` | Brokers counter-offers between bank and customer |
| Compliance | `compliance_agent.md` | Final synthetic compliance gate before approval |

All agents receive **only synthetic mock data** and their outputs carry no
real financial weight.

---

## Mocked External Services

| Service | Module | Real capability when un-mocked |
|---------|--------|-------------------------------|
| Notion MCP | `integrations/notion_mcp.py` | Create/query pages in a Notion database |
| Gmail | `integrations/gmail.py` | Send transactional emails via Gmail API |
| Slack | `integrations/slack.py` | Post messages to a Slack channel |
| Core Banking | `integrations/mock_services.py` | Account balances, credit reports (always mock) |

> **Core-banking and credit-bureau adapters are permanently mocked.**  There
> is intentionally no "live" path for these services — this project will never
> connect to real financial infrastructure.

---

## Development

```bash
# Lint
ruff check app tests

# Type-check
mypy app

# Run tests with coverage
pytest --cov=app --cov-report=term-missing
```

---

## License

MIT
