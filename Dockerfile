# Fitini — container image.
# Works as-is for Hugging Face Spaces (SDK: docker, app_port: 7860).
FROM python:3.11-slim

WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir .

# Runtime data in a writable, ephemeral directory (demo). Override with FITINI_DATA.
ENV FITINI_DATA=/tmp/fitini
RUN mkdir -p /tmp/fitini && chmod 777 /tmp/fitini

EXPOSE 7860
CMD ["fitini", "serve", "--host", "0.0.0.0", "--port", "7860"]
