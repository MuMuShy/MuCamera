FROM python:3.11-slim

WORKDIR /app

# Install FFmpeg (needed for LiveKit RTMP push)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all agent code
COPY *.py .

# Run agent
CMD ["python", "agent.py"]
