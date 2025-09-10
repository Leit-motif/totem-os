FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Copy only what's needed for the placeholder API
COPY apps/api/src ./apps/api/src

# Ensure Python can import the api package
ENV PYTHONPATH=/app/apps/api/src

CMD ["python", "-m", "api.main"]


