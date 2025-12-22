FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for aiortc
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    libavformat-dev \
    libavcodec-dev \
    libavdevice-dev \
    libavutil-dev \
    libswscale-dev \
    libswresample-dev \
    libavfilter-dev \
    libopus-dev \
    libvpx-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy agent code
COPY . .

# Run agent
CMD ["python", "agent.py"]
