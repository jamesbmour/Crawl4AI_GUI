FROM python:3.10-slim

# Set up a new user named "user" with user ID 1000 (Required by Hugging Face Spaces)
RUN useradd -m -u 1000 user

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Playwright and browser dependencies as root
RUN pip install playwright && playwright install-deps chromium

# Switch to the "user" user
USER user

# Set home to the user's home directory
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH

# Set the working directory to /app
WORKDIR $HOME/app

# Copy all application files from the repository
COPY --chown=user . $HOME/app

# Install Python dependencies
RUN pip3 install --upgrade pip
RUN pip3 install --no-cache-dir -r requirements.txt

# Install Playwright browsers (in the user's home directory)
RUN playwright install chromium

# Run Crawl4AI post-install setup
RUN crawl4ai-setup

# Expose the Streamlit port used by Hugging Face Spaces (7860)
EXPOSE 7860

# Run the Streamlit application
CMD ["streamlit", "run", "app.py", "--server.port=7860", "--server.address=0.0.0.0"]