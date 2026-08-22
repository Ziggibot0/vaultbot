# Dockerfile for testing VaultBot fresh installs and updates
# Simulates a clean Linux environment with Python, git, curl, node
#
# Usage:
#   docker build -t vaultbot-test .
#   docker run --rm vaultbot-test

FROM python:3.11-slim

# Install prerequisites
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    unzip \
    nodejs \
    npm \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user (simulates a real user environment)
RUN useradd -m -s /bin/bash vaultbotuser
USER vaultbotuser
WORKDIR /home/vaultbotuser

# Copy the test scripts
COPY --chown=vaultbotuser:vaultbotuser test_install.sh /home/vaultbotuser/test_install.sh
COPY --chown=vaultbotuser:vaultbotuser test_update.sh /home/vaultbotuser/test_update.sh
RUN chmod +x /home/vaultbotuser/test_install.sh /home/vaultbotuser/test_update.sh

# Default: run both tests
CMD ["/bin/bash", "-c", "/home/vaultbotuser/test_install.sh && echo '========================================' && /home/vaultbotuser/test_update.sh"]
