# Meridian Capital Partners (`ls_equity_fund`)

Plataforma local-first de research long/short equity e **paper trading** criada a partir dos prompts fornecidos.

> **Aviso**: apenas investigação, backtesting e paper trading. Não é aconselhamento financeiro. Live trading está desativado por defeito em `config.yaml`.

## Estrutura

```text
ls_equity_fund/
├── analysis/       # análise qualitativa via OpenRouter
├── cache/          # SQLite e caches locais (gitignored)
├── common/         # helpers comuns: config, logging, SQLite, dataframes
├── dashboard/      # dashboard Streamlit/JARVIS
├── data/           # ingestão de dados
├── execution/      # execução Alpaca paper/dry-run
├── factors/        # scoring/fatores
├── output/         # logs, CSVs e relatórios locais (gitignored)
├── portfolio/      # construção de portfolio
├── reporting/      # reporting/letters/tear sheets
├── risk/           # gestão de risco
├── tests/
├── config.yaml
├── pyproject.toml
└── run_*.py
```

## Setup local

Neste WSL usa `python3`:

```bash
cd /mnt/c/Users/Tonym/Desktop/hermes/ls_equity_fund
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -e ".[dev]"
cp .env.example .env
```

Edite `.env` apenas com as chaves necessárias. O ficheiro `.env` é gitignored.

## Login do dashboard

O dashboard Streamlit está protegido com o OIDC nativo do Streamlit (`st.login`, `st.user`, `st.logout`) e configurado para Clerk. Não faças commit de secrets reais.

1. No Clerk Dashboard, cria uma OAuth application para o dashboard.
2. Adiciona `http://localhost:8501/oauth2callback` aos Redirect URIs da OAuth application.
3. Ativa os scopes `openid`, `email` e `profile`.
4. Copia `.streamlit/secrets.example.toml` para `.streamlit/secrets.toml` e preenche `cookie_secret`, `client_id`, `client_secret` e `server_metadata_url` com o Discovery URL da OAuth application do Clerk.
5. Em `config.yaml`, adiciona o teu email Clerk a `dashboard.auth.allowed_emails` ou o domínio permitido a `dashboard.auth.allowed_domains`.

Sem `.streamlit/secrets.toml` e sem allowlist, o dashboard falha fechado e não mostra dados.

## OpenRouter para análise qualitativa

A camada `analysis` está configurada para `provider: openrouter`, para poder correr em cloud sem depender do Codex CLI local do Windows.

Config local:

```bash
cp .env.example .env
```

Preenche no `.env`:

```env
OPENROUTER_API_KEY=
OPENROUTER_MODEL=deepseek/deepseek-v4-flash:free
OPENROUTER_SITE_URL=http://localhost:8501
OPENROUTER_APP_NAME=Meridian JARVIS
```

Config relevante em `config.yaml`:

```yaml
analysis:
  provider: "openrouter"
  model_env: "OPENROUTER_MODEL"
  default_model: "deepseek/deepseek-v4-flash:free"
  openrouter_api_key_env: "OPENROUTER_API_KEY"
  openrouter_app_name: "Meridian JARVIS"
```

O modelo default é gratuito no OpenRouter no momento da configuração. Se deixar de estar disponível ou se os limites forem baixos, muda `OPENROUTER_MODEL` sem alterar o código.

## Deploy em container

O `Dockerfile` corre o dashboard com `python run_dashboard.py --serve`. Em produção, define `PORT` e `STREAMLIT_SERVER_ADDRESS=0.0.0.0`; no Northflank, a porta pública deve apontar para o `PORT` do container.

Não envies `.env` nem `.streamlit/secrets.toml` para a imagem. O entrypoint do container cria o ficheiro de secrets do Streamlit em runtime a partir destas variáveis secretas:

```env
STREAMLIT_AUTH_REDIRECT_URI=https://teu-dominio/oauth2callback
STREAMLIT_AUTH_COOKIE_SECRET=
CLERK_OAUTH_CLIENT_ID=
CLERK_OAUTH_CLIENT_SECRET=
CLERK_OIDC_METADATA_URL=
STREAMLIT_AUTH_PROMPT=consent
```

Também podes usar `STREAMLIT_AUTH_SECRETS_TOML` com o conteúdo TOML completo, mas as variáveis separadas são mais fáceis de gerir no Northflank.

## Comandos por camada

```bash
python3 run_data.py --dry-run --no-filings --no-13f
python3 run_scoring.py --dry-run --ticker AAPL
python3 run_analysis.py --dry-run --estimate-cost
python3 run_portfolio.py --dry-run
python3 run_risk_check.py --dry-run --stress
python3 run_execution.py --dry-run
python3 run_reporting.py --dry-run
python3 run_dashboard.py --dry-run
```

## Testes

```bash
python3 -m pytest -q
```
