FROM python:3.11-slim

WORKDIR /app

COPY remote/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD gunicorn -w 1 -b 0.0.0.0:$PORT remote.run:app
