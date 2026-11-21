# Blast Radius selector: answers every patch from the pinned sqlglot source and a
# precomputed coverage map. No tests are executed at run time.
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

ADD --checksum=sha256:93b2445f15f395b81c20556777ece19a6c7507df950f13fa9fac4a3be739af0d \
    https://github.com/tobymao/sqlglot/archive/eb11aec4be8cb2122afe713a4816662c3f0fb2fa.tar.gz \
    /tmp/sqlglot.tar.gz
RUN tar -xzf /tmp/sqlglot.tar.gz -C /tmp \
    && mv /tmp/sqlglot-eb11aec4be8cb2122afe713a4816662c3f0fb2fa /app/sqlglot \
    && rm /tmp/sqlglot.tar.gz

WORKDIR /app
COPY selector /app/selector
COPY maps /app/maps

ENV SQLGLOT_DIR=/app/sqlglot \
    MAPS=/app/maps/maps.pkl.gz \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN chmod -R a+rX /app

CMD ["python", "-m", "selector.main"]
