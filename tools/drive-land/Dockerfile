# CloudBTL drive-land — Google Drive(공유 드라이브) → CloudBTL 스트리밍 랜딩. Cloud Run Job, 키 없는 ADC(런타임 SA).
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl unzip ca-certificates && rm -rf /var/lib/apt/lists/* \
 && curl -fsSLo /tmp/rclone.zip https://downloads.rclone.org/rclone-current-linux-amd64.zip \
 && unzip -q /tmp/rclone.zip -d /tmp && cp /tmp/rclone-*-linux-amd64/rclone /usr/local/bin/rclone && chmod +x /usr/local/bin/rclone && rm -rf /tmp/rclone* \
 && pip install --no-cache-dir httpx>=0.27
WORKDIR /app
COPY rclone_land.py /app/rclone_land.py
COPY drive-land/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh
ENTRYPOINT ["/app/entrypoint.sh"]
