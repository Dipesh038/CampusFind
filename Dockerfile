# Use official lightweight Python 3.12 image
FROM python:3.12-slim

# Prevent Python from writing bytecode and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=development \
    DATABASE=/app/instance/campusfind.sqlite

# Set working directory inside container
WORKDIR /app

# Install dependencies first for efficient layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY campusfind/ ./campusfind/

# Create instance directory for persistent SQLite database
RUN mkdir -p /app/instance

# Copy entrypoint script and normalize Windows CRLF line endings
COPY entrypoint.sh .
RUN sed -i 's/\r$//' entrypoint.sh && chmod +x entrypoint.sh

# Expose the application port
EXPOSE 5000

# Set entrypoint to run migrations/initialization before server start
ENTRYPOINT ["/bin/sh", "./entrypoint.sh"]

# Default command using Gunicorn WSGI server
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", "campusfind:create_app()"]
