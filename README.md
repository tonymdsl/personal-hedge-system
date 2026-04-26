# personal-hedge-system

App local de analise pessoal de mercados com backend FastAPI, frontend Next.js e DuckDB local. O objetivo e criar uma plataforma privada estilo research terminal para uso proprio, com foco em credibilidade dos dados, metadata visivel e paper analytics.

Nao gere dinheiro de terceiros, nao promete retornos, nao executa ordens reais, nao usa APIs pagas e nao faz scraping do Financial Times. O modulo FT Research guarda apenas input manual do utilizador.

## Stack

- Backend: FastAPI + Python
- Frontend: Next.js + TypeScript + Tailwind + componentes estilo shadcn/ui
- Base local: DuckDB
- Dados: Stooq como fonte principal, fonte gratuita alternativa para alguns simbolos, e fallback deterministico de exemplo
- Graficos: Recharts

## Estrutura

```text
backend/
  app/
    main.py
    database.py
    config.py
    models/
    routers/
    services/
      analytics/
      data_sources/
  tests/
  requirements.txt
frontend/
  app/
    dashboard/
    watchlist/
    asset/[symbol]/
    ft-research/
    risk/
    report/
  components/
  lib/
  package.json
```

## Backend

```bash
cd backend
pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

Endpoints principais:

- `GET /health`
- `GET /api/watchlist`
- `POST /api/watchlist`
- `GET /api/prices/{symbol}`
- `POST /api/data/refresh`
- `GET /api/metrics/{symbol}`
- `GET /api/regime`
- `GET /api/dashboard`
- `GET /api/report/daily`
- `POST /api/ft-notes`
- `GET /api/ft-notes`

## Frontend

```bash
cd frontend
npm install
npm run dev
```

A app abre em `http://localhost:3000`. Por defeito, o frontend chama `http://127.0.0.1:8000`. Para mudar:

```bash
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
```

## Deploy no Northflank

O projeto esta preparado para dois combined services no Northflank, ambos a partir da raiz do repo:

- Backend: `Dockerfile.backend`, porta publica HTTP `8000`.
- Frontend: `Dockerfile.frontend`, porta publica HTTP `3000`.

Variaveis recomendadas:

Backend runtime environment:

```bash
PHS_DB_PATH=/app/data/personal_hedge.duckdb
PHS_ALLOWED_ORIGINS=https://<frontend-service>.code.run
```

Frontend build arguments e runtime environment:

```bash
NEXT_PUBLIC_API_BASE_URL=https://<backend-service>.code.run
```

Em Next.js, `NEXT_PUBLIC_API_BASE_URL` tambem tem de existir como build argument, porque chamadas client-side sao compiladas no bundle. Se a pagina abrir sem estilos ou sem interatividade, confirme que o container copiou `.next/static` para a imagem standalone.

## Dados e fontes

A watchlist inicial e criada automaticamente se a base estiver vazia:

`SPY`, `QQQ`, `TLT`, `GLD`, `AAPL`, `MSFT`, `NVDA`, `AMZN`, `GOOGL`, `META`.

O backend usa Stooq como fonte principal para precos historicos de acoes/ETFs dos EUA. Se Stooq falhar ou o ativo nao for suportado, tenta uma fonte gratuita alternativa para alguns simbolos e, se ainda assim falhar, usa `sample_data` deterministico para a app nunca abrir vazia.

Conversao para Stooq:

- `AAPL` -> `aapl.us`
- `MSFT` -> `msft.us`
- `SPY` -> `spy.us`
- `QQQ` -> `qqq.us`

`BTC-USD` e ignorado na watchlist inicial porque nao e suportado pelo conector Stooq deste MVP. Se for adicionado manualmente, pode funcionar apenas se uma fonte gratuita alternativa devolver dados.

Todas as vistas de mercado mostram metadata:

- `source`
- `last_updated`
- `data_range_start`
- `data_range_end`
- `price_type`
- `is_sample_data`

Quando `is_sample_data` for `true`, a interface mostra um aviso claro. O sistema nao mistura dados reais e sample para o mesmo ativo sem flag visivel.

## Paginas

- `/dashboard`: regime atual, cards de metricas, performance de SPY, watchlist, maiores movimentos, metadata e notas FT recentes.
- `/watchlist`: tabela de ativos, formulario para adicionar ativo, metadata de fonte e botao de atualizacao de dados.
- `/asset/[symbol]`: pagina dinamica para qualquer ativo da watchlist, com grafico de preco, metricas, metadata, drawdown e retornos diarios.
- `/ft-research`: formulario manual para notas FT, tags por virgula, portfolio relevance e filtros por ativo, sentimento, impacto, horizonte e relevance.
- `/risk`: exposicao equal-weight, portfolio volatility, portfolio drawdown, risk contribution e alertas.
- `/report`: relatorio diario com regime, top movers, alertas, watchlist, notas FT e implicacoes de portfolio baseadas em regras.

## Limitacoes

- A carteira e assumida como equal-weight porque ainda nao ha posicoes reais configuradas.
- O modulo de risco calcula exposicao, volatilidade e contribuicao de risco apenas para investigacao.
- As notas FT sao input manual: nao ha scraping, nao ha armazenamento automatico de texto completo e nao ha tentativa de contornar paywalls.
- Dados gratuitos podem falhar, ter atrasos ou diferir de fornecedores profissionais.
- O fallback `sample_data` existe para manter a app utilizavel; nao deve ser tratado como dado de mercado real.

## Testes e verificacao

Backend:

```bash
cd backend
python -m pytest tests -q
```

Frontend:

```bash
cd frontend
npm run typecheck
npm run build
```

## Nota legal

Este projeto e apenas para investigacao pessoal e paper analytics only. Nao e aconselhamento financeiro, nao promete retornos, nao gere dinheiro de terceiros e nao executa ordens.
