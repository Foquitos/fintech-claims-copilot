FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv

# Dependencies first: cached as long as requirements.txt does not change.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
# data/ must exist and belong to appuser before the volume mounts there: Docker initializes a
# new named volume with the image's ownership; otherwise it is root-owned and not writable.
RUN useradd --create-home appuser && mkdir -p /srv/data/index && chown -R appuser /srv
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
