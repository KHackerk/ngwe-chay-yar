FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p data/uploads data/photos

ENV PORT=5000
EXPOSE 5000

CMD gunicorn --bind 0.0.0.0:$PORT --workers 1 app:app
