# Use GDAL base image.å
FROM ghcr.io/osgeo/gdal:ubuntu-small-3.10.3

ARG DEV=false

# Don't use old pygeos
ENV USE_PYGEOS=0

# Install system dependencies (this layer rarely changes)
RUN apt-get update && apt-get install -y \
    python3-dev \
    git \
    curl \
    ca-certificates \
    build-essential \
    libsqlite3-dev \
    && apt-get autoclean \
    && apt-get autoremove \
    && rm -rf /var/lib/{apt,dpkg,cache,log}

# Install AWS CLI (this layer rarely changes)
RUN cd /tmp && \
    curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip" && \
    unzip awscliv2.zip && \
    ./aws/install && \
    rm -rf awscliv2.zip aws

# Install tippecanoe
RUN git clone https://github.com/felt/tippecanoe.git \
    && cd tippecanoe \
    && make -j \
    && make install \
    && cd .. && rm -rf tippecanoe

# Install UV (this layer rarely changes)
ADD https://astral.sh/uv/install.sh /uv-installer.sh
RUN sh /uv-installer.sh && rm /uv-installer.sh
ENV PATH="/root/.local/bin/:$PATH"

# Make bash the default shell
RUN ln -sf /bin/bash /bin/sh

# Create a folder and copy this project's code there.
WORKDIR /code
COPY . .

# Install dependencies (with UV) in a separate layer
RUN --mount=type=cache,target=/root/.cache/uv \
    if [ "$DEV" = "true" ] ; then \
        uv sync --no-progress; \
    else \
        uv sync --no-dev --no-progress; \
    fi

ENV PATH="/code/.venv/bin:$PATH"

# Smoketest
RUN csdr --help
