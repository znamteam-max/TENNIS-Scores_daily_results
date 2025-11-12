FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV TZ=Europe/Helsinki
ENV POLL_SECONDS=75
ENV DATA_SOURCE=sofascore

CMD ["python", "-u", "bot.py"]
