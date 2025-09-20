FROM python:3.11-slim

WORKDIR /app

# Install system dependencies with version pinning and no recommended packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc=4:* \
    netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose ports
EXPOSE 8000 8501

# Create volume for logs
VOLUME /app/logs

# Create entrypoint script
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

ENTRYPOINT ["./entrypoint.sh"]