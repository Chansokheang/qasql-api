FROM python:3.11-slim

WORKDIR /app

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

# Default command
CMD ["python", "app.py", "--host", "0.0.0.0", "--port", "8000"]
