# Stage 1: Build dependencies
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    apache2 \
    apache2-dev \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir --upgrade pip && \
    /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# Stage 2: Runtime image
FROM python:3.12-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    apache2 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv

RUN /opt/venv/bin/mod_wsgi-express module-config > /etc/apache2/mods-available/wsgi.load \
    && a2enmod wsgi \
    && echo "ServerName localhost" >> /etc/apache2/apache2.conf \
    && echo "PassEnv DB_HOST DB_PORT DB_NAME DB_USER DB_PASSWORD" >> /etc/apache2/conf-available/passenv.conf \
    && a2enconf passenv \
    && a2dissite 000-default.conf

COPY apache-vhost.conf /etc/apache2/sites-available/web_service.conf
RUN a2ensite web_service.conf

COPY db.py web_service.py web_service.wsgi ./
COPY templates ./templates

RUN mkdir -p /app/uploads && chown -R www-data:www-data /app

EXPOSE 80

CMD ["sh", "-c", "chown -R www-data:www-data /app/uploads && exec apache2ctl -D FOREGROUND"]
