FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8501 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY config.yaml ./
COPY run_*.py ./
COPY analysis ./analysis
COPY autopilot ./autopilot
COPY common ./common
COPY dashboard ./dashboard
COPY data ./data
COPY execution ./execution
COPY factors ./factors
COPY ls_equity_fund ./ls_equity_fund
COPY portfolio ./portfolio
COPY reporting ./reporting
COPY risk ./risk
COPY ops ./ops

RUN python -m pip install --upgrade pip \
    && python -m pip install -e ".[all]" \
    && chmod +x ops/docker-entrypoint.sh

EXPOSE 8501

ENTRYPOINT ["ops/docker-entrypoint.sh"]
CMD ["python", "run_dashboard.py", "--serve"]
