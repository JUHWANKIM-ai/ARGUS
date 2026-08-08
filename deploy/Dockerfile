FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY src/ ./src/
RUN pip install --no-cache-dir -e .

COPY artifacts/ ./artifacts/

EXPOSE 80
CMD ["uvicorn", "argus.api.server:app", "--host", "0.0.0.0", "--port", "80"]
