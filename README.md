# 🦠 Pandemic Data Platform

Plataforma completa de análise epidemiológica do Brasil — da ingestão de dados
públicos até API em produção, modelos estatísticos em R e dashboard executivo.

> **Narrativa:** simular os picos de acesso durante surtos de COVID-19, exatamente
> quando os dados são mais críticos para a tomada de decisão pública.

---

## Arquitetura

```
                         ┌─────────────────────────────────────────┐
                         │            pandemic-data-platform        │
                         └─────────────────────────────────────────┘

  Fontes de dados públicos          ETL (Python)           PostgreSQL
  ┌──────────────────┐         ┌──────────────┐         ┌──────────────┐
  │ brasil.io        │────────►│ etl/ingest.py│────────►│              │
  │ OpenDataSUS      │         │              │         │  covid_cases │
  │ IBGE SIDRA API   │────────►│ etl/         │────────►│  vaccination │
  │ BCB SGS API      │         │ economics.py │         │  economic_   │
  └──────────────────┘         └──────────────┘         │  indicators  │
                                                         │  forecast_   │
                                                         │  results     │
                                                         └──────┬───────┘
                                                                │
                     ┌──────────────────────────────────────────┘
                     │
          ┌──────────▼──────────┐         ┌────────────────────┐
          │   FastAPI  :8000    │◄────────►│  R plumber  :8001  │
          │                     │  HTTP    │                    │
          │  GET /cases/brasil  │  POST    │  /forecast         │
          │  GET /cases/parana  │ /forecast│  ARIMA             │
          │  GET /cases/maringa │ /correl. │  Prophet           │
          │  GET /vaccination   │          │  Holt-Winters      │
          │  GET /forecast      │          │  Ensemble          │
          │  GET /economics     │          │  lm() OLS          │
          │  GET /health        │          │  Granger test      │
          └──────────┬──────────┘          └────────────────────┘
                     │
          ┌──────────▼──────────┐         ┌────────────────────┐
          │ Streamlit  :8501    │         │ Prometheus  :9090  │
          │ Dashboard público   │         │ + Grafana   :3000  │
          └─────────────────────┘         └────────────────────┘

  Kubernetes (k3d local)
  ┌──────────────────────────────────────────────────────────────────┐
  │  HPA pandemic-api     CPU 60% → scale 2→10 replicas             │
  │  HPA pandemic-r-service CPU 70% → scale 1→4 replicas            │
  │  PodDisruptionBudget  minAvailable: 1                            │
  │  securityContext      runAsNonRoot + readOnlyRootFilesystem      │
  └──────────────────────────────────────────────────────────────────┘
```

---

## Stack

| Camada | Tecnologia | Papel |
|---|---|---|
| **Ingestão** | Python 3.11 · httpx · SQLAlchemy · asyncpg | ETL assíncrono e idempotente |
| **Modelos** | R 4.3.3 · prophet · forecast · lmtest · plumber | ARIMA, Prophet, Holt-Winters, OLS, Granger |
| **API** | FastAPI · Pydantic v2 · uvicorn | REST com OpenAPI automático |
| **Banco** | PostgreSQL 16 (Supabase free tier) | Séries temporais, UPSERTs |
| **Container** | Docker · multi-stage build · tini | Imagens leves, não-root |
| **Orquestração** | k3d · kubectl · HPA | Kubernetes local, autoscaling |
| **Observabilidade** | Prometheus · Grafana · structlog | Métricas, alertas de surto |
| **Dashboard** | Streamlit · Plotly | Vitrine pública |
| **CI/CD** | GitHub Actions · GHCR · trivy | Lint, teste, build, deploy, scan |
| **Testes** | pytest · pytest-asyncio · httpx | 40+ testes unitários e de integração |

---

## Início rápido

### Pré-requisitos

```bash
docker --version    # >= 24
docker compose version  # >= 2.20
```

### 1. Clonar e configurar

```bash
git clone https://github.com/SEU_USUARIO/pandemic-data-platform.git
cd pandemic-data-platform
cp .env.example .env
# Edite .env e preencha DATABASE_URL (Supabase ou PostgreSQL local)
```

### 2. Subir toda a stack

```bash
docker compose up --build
```

Isso sobe em ordem:
1. **PostgreSQL** → cria o banco e aguarda healthcheck
2. **R service** → compila pacotes R (primeira vez ~15 min; layers cacheadas depois)
3. **API** → aguarda DB + R service healthy, cria as tabelas
4. **ETL** → executa ingestão inicial (brasil.io + IBGE + BCB)
5. **Dashboard** → aguarda API healthy
6. **Prometheus + Grafana** → coleta métricas automaticamente

### 3. Acessar os serviços

| Serviço | URL | Credenciais |
|---|---|---|
| FastAPI docs | http://localhost:8000/docs | — |
| Streamlit dashboard | http://localhost:8501 | — |
| Grafana | http://localhost:3000 | admin / admin |
| Prometheus | http://localhost:9090 | — |

---

## Estrutura do repositório

```
pandemic-data-platform/
│
├── etl/                        # Ingestão de dados públicos
│   ├── models.py               # SQLAlchemy ORM (4 tabelas)
│   ├── ingest.py               # brasil.io + OpenDataSUS (streaming CSV)
│   ├── economics.py            # IBGE SIDRA + BCB SGS
│   └── config.py               # Settings via env vars
│
├── r-service/                  # Microserviço R (plumber)
│   ├── plumber.R               # 5 endpoints REST
│   ├── entrypoint.R            # Inicialização + tini signal handling
│   ├── install_packages.R      # Instalação + renv snapshot
│   ├── renv.lock               # Lockfile de reprodutibilidade
│   ├── Dockerfile              # Multi-stage, libv8 copiada do builder
│   └── models/
│       ├── forecast.R          # ARIMA · Prophet · Holt-Winters · Ensemble
│       └── correlation.R       # OLS · Pearson · Spearman · Granger
│
├── api/                        # FastAPI
│   ├── main.py                 # App + middleware + Prometheus
│   ├── config.py               # pydantic-settings
│   ├── dependencies.py         # DB session pool
│   ├── routers/                # cases · vaccination · forecast · economics · health
│   ├── services/               # Lógica + r_client.py (TTL cache + retry)
│   └── models/responses.py     # Pydantic response schemas
│
├── dashboard/                  # Streamlit público
│   ├── app.py                  # Home: KPIs + gráficos
│   ├── api_client.py           # Wrapper com @st.cache_data
│   └── pages/
│       ├── 1_previsao.py       # Forecast interativo (todos os modelos)
│       ├── 2_vacinacao.py      # Doses diárias + acumulado
│       └── 3_economia.py       # Correlação + OLS + Granger
│
├── k8s/                        # Manifests Kubernetes
│   ├── namespace.yaml          # ResourceQuota + LimitRange
│   ├── configmap.yaml          # Configuração não-sensível
│   ├── secret.yaml             # Template (não comitar valores reais)
│   ├── deployment.yaml         # API + R service + PodDisruptionBudget
│   ├── service.yaml            # NodePort (API, dashboard) + ClusterIP (R)
│   ├── hpa.yaml                # HPA 60% CPU → surge simulation
│   └── ingress.yaml            # Traefik (k3d) + strip-prefix middleware
│
├── monitoring/
│   ├── prometheus.yml          # Scrape config
│   ├── alerts.yml              # 6 alertas (error rate, latência, surto)
│   └── grafana/provisioning/   # Datasource + dashboard auto-provisionados
│
├── tests/
│   ├── conftest.py             # SQLite in-memory, mock R service, fixtures
│   ├── test_cases.py           # 12 testes de casos
│   ├── test_forecast.py        # 8 testes de forecast
│   ├── test_health.py          # 4 testes de health
│   └── test_etl.py             # 18 testes de ETL (parse, upsert, idempotência)
│
├── .github/workflows/
│   ├── ci.yml                  # Lint + teste + build + scan (trivy)
│   └── deploy.yml              # CD para k3d (smoke test + rollback automático)
│
├── docker-compose.yml          # Stack completa (7 serviços)
├── pytest.ini
└── .env.example
```

---

## ETL

### Executar manualmente

```bash
# Todos os estados
docker compose run --rm etl python -m etl.ingest

# Apenas Paraná
docker compose run --rm etl python -m etl.ingest PR

# Indicadores econômicos
docker compose run --rm etl python -m etl.economics
```

### Fontes de dados

| Fonte | Dataset | Endpoint |
|---|---|---|
| [brasil.io](https://brasil.io) | Casos por município | `GET /api/dataset/covid19/caso_full/` |
| [OpenDataSUS](https://opendatasus.saude.gov.br) | Vacinação | CSV S3 por estado |
| [IBGE SIDRA](https://apisidra.ibge.gov.br) | IPCA (tab. 1737) + Desemprego (tab. 6381) | REST |
| [BCB SGS](https://dadosabertos.bcb.gov.br) | Selic (432) · Meta Selic (28750) | REST |

---

## API

Documentação interativa: **http://localhost:8000/docs**

```
GET /health                     Liveness + readiness probe
GET /cases/brasil               Casos por UF — todos os estados
GET /cases/parana               Casos por município — PR
GET /cases/maringa              Casos — Maringá (IBGE 4115200)
GET /vaccination                Doses diárias por estado e tipo
GET /forecast                   Previsão via R (ARIMA/Prophet/HW/Ensemble)
GET /economics                  Correlação COVID × Selic/IPCA/Desemprego
GET /metrics                    Prometheus metrics
```

### Exemplo de uso

```bash
# Previsão Prophet para o Brasil, 30 dias
curl "http://localhost:8000/forecast?scope=brasil&model=prophet&horizon=30" | jq .

# Casos de Maringá em junho/2021
curl "http://localhost:8000/cases/maringa?start_date=2021-06-01&end_date=2021-06-30" | jq .

# Análise de correlação COVID × economia
curl "http://localhost:8000/economics?scope=brasil" | jq .granger
```

---

## Modelos estatísticos em R

O microserviço R expõe três endpoints `POST` consumidos internamente pela FastAPI:

| Endpoint | Função R | Pacote |
|---|---|---|
| `POST /forecast` (model=arima) | `run_arima()` | `forecast::auto.arima()` |
| `POST /forecast` (model=prophet) | `run_prophet()` | `prophet` (Meta) |
| `POST /forecast` (model=holtwinters) | `run_holtwinters()` | `forecast::hw()` |
| `POST /forecast` (model=ensemble) | `run_ensemble()` | média dos 3 |
| `POST /smooth` | `run_moving_average()` | `zoo::rollmean()` |
| `POST /correlation` | `run_full_correlation()` | `lm()` · `lmtest` · `broom` |

Feriados brasileiros (2020–2024) são incluídos como componente do Prophet.

---

## Testes

```bash
# Instalar dependências de teste
pip install -r etl/requirements.txt -r api/requirements.txt -r tests/requirements.txt

# Executar toda a suite
pytest

# Com cobertura
pytest --cov=api --cov=etl --cov-report=term-missing

# Apenas testes rápidos (excluindo lentos)
pytest -m "not slow"
```

A suite não requer o R service nem um Postgres externo — usa SQLite in-memory e
um `AsyncMock` para o cliente HTTP do R.

---

## Kubernetes (k3d local)

### Setup inicial

```bash
# Instalar k3d
curl -s https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh | bash

# Criar cluster com port-forward na porta 80
k3d cluster create pandemic-cluster --agents 2 -p "80:80@loadbalancer"

# Adicionar entrada DNS local
echo "127.0.0.1 pandemic.local" | sudo tee -a /etc/hosts

# Instalar metrics-server (necessário para o HPA)
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml

# Deploy completo
kubectl apply -f k8s/
```

### Simular pico de surto

```bash
# Gerar carga e observar o HPA escalar automaticamente
kubectl run load-generator --rm -i --tty \
  --image=busybox --restart=Never -n pandemic \
  -- /bin/sh -c "while true; do wget -q -O- http://pandemic-api:8000/cases/brasil; done"

# Em outro terminal, observar o scaling
kubectl get hpa -n pandemic -w
```

O HPA inicia scale-out quando CPU > 60%, adicionando até 4 pods por evento com
janela de estabilização de 30 segundos.

---

## CI/CD

```
push → main
  │
  ├─ lint-python   ruff + mypy
  ├─ lint-r        lintr
  ├─ test-python   pytest (PostgreSQL service container)
  │
  ├─ build-api     docker build → GHCR (tag: sha-XXXXXXX + latest)
  ├─ build-r       docker build → GHCR (com GHA cache para Stan .o)
  │
  └─ security-scan trivy → SARIF → GitHub Security tab
       │
       └─ deploy.yml (self-hosted runner com k3d)
            ├─ kubectl apply -f k8s/
            ├─ rollout status (timeout 3/5 min)
            ├─ smoke test GET /health
            └─ rollback automático se smoke test falhar
```

---

## Variáveis de ambiente

Veja `.env.example` para a lista completa. Variáveis obrigatórias:

| Variável | Descrição |
|---|---|
| `DATABASE_URL` | DSN asyncpg: `postgresql+asyncpg://user:pass@host:5432/db` |

Variáveis opcionais relevantes:

| Variável | Padrão | Descrição |
|---|---|---|
| `BRASILIO_TOKEN` | — | Token para download bulk CSV (sem token usa API paginada) |
| `VACCINATION_STATES` | `PR` | Estados para ingerir vacinação (separados por vírgula) |
| `R_SERVICE_URL` | `http://r-service:8001` | URL interna do microserviço R |
| `CRAN_SNAPSHOT_DATE` | `2024-06-01` | Data do snapshot CRAN (Posit PPM) |

---

## Decisões técnicas

| Decisão | Alternativa considerada | Motivo da escolha |
|---|---|---|
| R via microserviço (plumber) | rpy2 (R dentro do Python) | Isolamento de processos, restartável independentemente, aparece no diagrama de arquitetura |
| SQLAlchemy async + asyncpg | SQLModel, Tortoise ORM | Controle explícito do SQL, fácil de perfilar no Grafana |
| Paginação em todos os endpoints | Retornar tudo | Séries de casos do Brasil têm >300k linhas |
| TTL cache no r_client.py | Redis | Sem dependência extra; substituível trivialmente |
| tini como PID 1 | CMD direto | SIGTERM propagado corretamente em rolling updates |
| renv.lock + CRAN snapshot | Apenas requirements | Dois níveis de reprodutibilidade para o ambiente R |
| `--no-install-suggests` | Apenas `--no-install-recommends` | Reduz superfície de ataque da imagem runtime |

---

## Roadmap v1.1

Melhorias documentadas — não bloqueiam publicação, mas fortalecem o projeto:

| Item | Descrição |
|---|---|
| **Scheduler ETL** | Substituir o one-shot `etl` container por APScheduler com cron nightly (`0 3 * * *`). O `docker-compose.yml` já tem `restart: on-failure` como paliativo. |
| **OpenDataSUS URL** | Validar URL atual do S3 da SARG/2021. Executar `etl/ingest.py` na primeira vez e ajustar o template conforme a resposta real. |
| **CORS restrito** | `CORS_ORIGINS=*` é intencional para portfólio público. Em produção, definir `CORS_ORIGINS=https://pandemic.seudominio.com` via `.env`. |
| **trivy exit-code** | `exit-code: "0"` faz o scan reportar sem bloquear. Trocar para `"1"` quando o projeto for para produção real. |
| **mypy full** | Adicionar `needs: [type-check]` em `test-python` quando asyncpg/sqlalchemy completarem os stubs. Monitorar [sqlalchemy/issues/6810](https://github.com/sqlalchemy/sqlalchemy/issues/6810). |
| **testthat para R** | Adicionar `r-service/tests/` com `testthat` cobrindo `run_arima()`, `run_prophet()` e `run_full_correlation()`. |
| **renv.lock real** | Após o primeiro `docker build`, extrair e comitar o `renv.lock` gerado (instruções no próprio arquivo). |

## Gerar o renv.lock real

O `r-service/renv.lock` incluso é um placeholder. Para gerar o lockfile com as versões exatas após a primeira build:

```bash
# Extrai o renv.lock gerado pelo builder stage
docker build --target builder \
  --output type=local,dest=./r-service/renv-out \
  -f r-service/Dockerfile r-service/

cp r-service/renv-out/install/renv.lock r-service/renv.lock
git add r-service/renv.lock
git commit -m "chore: add real renv.lock after initial build"
```

---

## Licença

MIT — use à vontade para seu próprio portfólio ou projetos.
