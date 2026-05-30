FROM python:3.12-alpine

RUN apk add --no-cache iproute2
WORKDIR /app
COPY socks5-proxy.py entrypoint.sh ./
RUN chmod +x entrypoint.sh socks5-proxy.py
EXPOSE 1080

ENTRYPOINT ["/app/entrypoint.sh"]
