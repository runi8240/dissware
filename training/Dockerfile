FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/training

CMD ["python", "/app/training/train_logreg.py", "--help"]
