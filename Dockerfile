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
EXPOSE 8000

# Use uvicorn directly with better settings for stability
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-keep-alive", "30", "--limit-concurrency", "100"]
