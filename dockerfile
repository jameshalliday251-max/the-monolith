FROM python:3.9-slim

WORKDIR /app

# Install Dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy Application Core
COPY app.py .
COPY index.html .
COPY reader.html .

# Create Archive Directory
RUN mkdir -p /app/library

# Expose Interface Port
EXPOSE 9696

# Initialize System
CMD ["python", "app.py"]