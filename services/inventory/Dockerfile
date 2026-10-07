FROM python:3.11-slim

WORKDIR /app

# install requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# copy code
COPY . .

# Expose port 8000
EXPOSE 8000

# start service
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]