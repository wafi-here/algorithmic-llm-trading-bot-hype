FROM python:3.10-slim

WORKDIR /app

# Install system dependencies needed for python compiling and network requests
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy and install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download VADER lexicon offline within the image to save runtime dependencies
RUN python -c "import nltk; nltk.download('vader_lexicon', quiet=True)"

# Copy app source code
COPY . /app/backend

# Set python path to allow imports of backend module
ENV PYTHONPATH=/app

EXPOSE 8000

# Run FastAPI uvicorn server
CMD ["python", "backend/main.py"]
