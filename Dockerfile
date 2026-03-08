FROM python:3.11-slim

WORKDIR /app

# Install curl for healthcheck
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY app.py .
COPY models.py .
COPY auth.py .

# Create directories
RUN mkdir -p /data /app/qasql_api_output

# Expose port
EXPOSE 9001

# Use gunicorn with uvicorn workers (better for production)
CMD ["gunicorn", "app:app", "-k", "uvicorn.workers.UvicornWorker", "-w", "4", "-b", "0.0.0.0:8000", "--timeout", "120", "--graceful-timeout", "60", "--keep-alive", "30"]
