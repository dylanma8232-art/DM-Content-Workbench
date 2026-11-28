FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DM_DATA_DIR=/data
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg poppler-utils tesseract-ocr tesseract-ocr-chi-sim && rm -rf /var/lib/apt/lists/* && useradd --create-home --uid 10001 workbench && install -d -o workbench /data
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY account_reporting.py ./
COPY workflow_engine.py service_credentials.py draft_archive.py app.py integrations.py connections.py extraction.py analysis.py backup.py collection.py references.py publishing.py collection_tasks.py collection_reader.py collection_browser.py collection_depth.py content_media.py content_understanding.py content_pipeline.py content_image_store.py processing_settings.py online_libraries.py local_ai.py local_asr.py ./
COPY web ./web
COPY tools/publish-extension ./tools/publish-extension
USER workbench
EXPOSE 8765
HEALTHCHECK CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8765/api/projects', timeout=3).close()"
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8765"]
