# The browser image. Kept away from the API container deliberately: Chromium
# wants several times the memory the API needs, and a browser competing for a
# gigabyte with the lane that answers her voice note loses it for both.
FROM mcr.microsoft.com/playwright/python:v1.49.1-noble

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium --with-deps

COPY . .

# One request at a time. A browser is stateful and two of them in a gigabyte is
# how this falls over under exactly the load it was built for.
CMD exec uvicorn app:app --host 0.0.0.0 --port ${PORT} --workers 1
