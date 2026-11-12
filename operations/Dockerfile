FROM registry.cn-shanghai.aliyuncs.com/zhinian-software/xianyu-backend-web:latest
USER root
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-chi-sim \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir 'pypdf>=5,<7'
ENV PYTHONPATH=/app:/app/backend-web
WORKDIR /app
COPY operations /app/operations
COPY common/services/pricing_policy.py /app/common/services/pricing_policy.py
COPY common/services/ai_bargaining.py /app/common/services/ai_bargaining.py
COPY common/services/ai_reply_recovery.py /app/common/services/ai_reply_recovery.py
COPY common/services/xianyu_publish_video.py /app/common/services/xianyu_publish_video.py
COPY common/services/publish_execution_service.py /app/common/services/publish_execution_service.py
COPY backend-web/app/services/xianyu_direct_publisher.py /app/backend-web/app/services/xianyu_direct_publisher.py
COPY backend-web/app/services/xianyu_item_edit_service.py /app/backend-web/app/services/xianyu_item_edit_service.py
CMD ["python", "-m", "operations.server"]
