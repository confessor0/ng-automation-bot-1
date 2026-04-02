# Use official Python runtime
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies if needed (none for now)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose port (Render will override this with its own PORT env var)
EXPOSE 5055

# Set environment to Production
ENV PYTHONUNBUFFERED=1
ENV PORT=5055
ENV ON_DEMAND_MODE=true

# Command to run both the bot and web server (bot.py starts web_server in a thread)
CMD ["python", "bot.py"]
