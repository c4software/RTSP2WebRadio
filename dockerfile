FROM python:3.12-alpine

RUN apk add --no-cache ffmpeg

WORKDIR /app

RUN pip install --no-cache-dir flask

COPY app.py .

EXPOSE 5000

CMD ["python", "app.py"]
