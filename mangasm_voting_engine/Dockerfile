FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

ENV PORT=8000
CMD ["uvicorn", "mangasm_voting_engine.app:app", "--host", "0.0.0.0", "--port", "8000"]
